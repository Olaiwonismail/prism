"""PySide6 control window (replaces the Tkinter stopgap).

Top of the window mirrors a two-pill layout: a Model picker and a Microphone
picker side by side, with a live scope below them: a scrolling waveform that
draws the raw mic level in grey and the cleaned output level in green on top,
so the grey visible above the green is the noise being stripped in real time.
Under that sit the hero power toggle (with a breathing glow while filtering)
and the settings (AI denoiser on/off, strength, readings).

Qt's event loop owns the main thread; audio runs on PortAudio's callback thread
inside AudioEngine. A QTimer polls display-only ``engine.*`` values at ~30 fps --
it only *reads* pre-computed numbers, so it never touches the audio callback or
does DSP.
"""

from collections import deque

from PySide6.QtCore import (
    Property, QEasingCurve, QPropertyAnimation, QRectF, Qt, QTimer,
)
from PySide6.QtGui import QColor, QPainter, QPen
from PySide6.QtWidgets import (
    QApplication, QCheckBox, QComboBox, QFrame, QGraphicsDropShadowEffect,
    QHBoxLayout, QLabel, QPushButton, QSizePolicy, QSlider, QVBoxLayout,
    QWidget,
)

import sounddevice as sd

from . import audio

_POLL_MS = 33             # UI refresh interval (~30 fps, also the scope scroll rate)

# Denoiser models offered in the picker: (display label, config.DENOISER value).
_DENOISER_OPTIONS = [
    ("RNNoise", "rnnoise"),
    ("GTCRN", "gtcrn"),
    ("DeepFilterNet", "deepfilternet"),
]
# Map a loaded stage's .name back to its picker label (see _active_model_label).
_NAME_TO_LABEL = {"RNNoise": "RNNoise", "GTCRN": "GTCRN",
                  "DeepFilterNet3": "DeepFilterNet"}

# Shared palette (QSS below + custom-painted widgets read from here).
_ACCENT = "#3d8bd4"
_ACCENT_BRIGHT = "#5fa8e8"
_PANEL = "#161a26"
_PANEL_EDGE = "#232838"
_RAW_BAR = QColor(150, 156, 175, 80)     # raw mic level (translucent grey)
_CLEAN_BAR = QColor(61, 139, 212, 235)   # cleaned output level
_IDLE_BAR = QColor(150, 156, 175, 150)   # passthrough level (no accent: nothing filtered)

# Dark theme. Kept in one string so the whole window shares one look.
_QSS = """
* { color: #e6e8ee; font-family: 'Segoe UI'; font-size: 13px; }
QWidget#root { background: #0f1119; }
QLabel#title { font-size: 17px; font-weight: 700; color: #dfe3ee; letter-spacing: 1px; }
QLabel#heroState { font-size: 14px; font-weight: 600; letter-spacing: 2px; }
QLabel#status { color: #8b90a0; }
QLabel#divider { color: #4b5060; font-size: 11px; letter-spacing: 3px; }
QLabel[class="fieldcap"] { color: #8b90a0; font-size: 11px; font-weight: 600; }
QLabel[class="muted"] { color: #8b90a0; }
QLabel[class="reading"] { color: #cfd3de; }
QLabel[class="legend"] { color: #6d7284; font-size: 11px; }

QFrame#spectrum {
    border: none; max-height: 3px; min-height: 3px; border-radius: 1px;
    background: qlineargradient(x1:0, y1:0, x2:1, y2:0,
        stop:0 #ff6b6b, stop:0.25 #ffd166, stop:0.5 #37e08f,
        stop:0.75 #4dabf7, stop:1 #b57bff);
}

QPushButton#hero {
    background: #1a1f2e; border: 2px solid #2c3242; border-radius: 52px;
    font-size: 40px; font-family: 'Segoe UI Symbol'; color: #7d8296;
}
QPushButton#hero:hover { border-color: #3a4152; }
QPushButton#hero:checked {
    background: qradialgradient(cx:0.5, cy:0.5, radius:0.7,
        stop:0 #16334f, stop:1 #10243a);
    border-color: #3d8bd4; color: #5fa8e8;
}

/* Pill-shaped pickers (the two dropdowns up top share this look). */
QComboBox {
    background: #1c2030; border: 1px solid #2c3242; border-radius: 18px;
    padding: 9px 16px; min-height: 18px;
}
QComboBox:hover { border-color: #3a4152; }
QComboBox::drop-down { border: none; width: 26px; }
QComboBox QAbstractItemView {
    background: #1c2030; border: 1px solid #2c3242; border-radius: 6px;
    selection-background-color: #3d8bd4; outline: none;
}

QSlider::groove:horizontal { height: 4px; background: #2c3242; border-radius: 2px; }
QSlider::sub-page:horizontal { background: #3d8bd4; border-radius: 2px; }
QSlider::handle:horizontal {
    width: 16px; margin: -7px 0; border-radius: 8px; background: #e6e8ee;
}

QFrame#hline { background: #232838; max-height: 1px; border: none; }
"""


class LevelScope(QWidget):
    """Scrolling dual-level waveform: raw mic level vs. cleaned output level.

    Each poll tick pushes one (raw, clean, filtering) sample; paintEvent draws
    the history as mirrored vertical bars around a dotted centerline. The raw
    level is a translucent grey bar with the clean level painted over it in
    green, so the grey sticking out past the green *is* the removed noise --
    the meter tells the product's whole story at a glance. In passthrough the
    bars go solid grey (nothing is being filtered, so nothing is green).

    Display only: it reads two floats per tick and never touches audio.
    """

    _BAR_W = 2
    _GAP = 1

    def __init__(self):
        super().__init__()
        self.setMinimumHeight(110)
        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
        # Enough columns for any sane window width; extras just scroll off.
        self._history = deque(maxlen=400)

    def push(self, raw_db, clean_db, filtering):
        self._history.append((self._amp(raw_db), self._amp(clean_db), filtering))
        self.update()

    @staticmethod
    def _amp(db):
        """Map dBFS to a 0..1 bar height, gently curved so quiet levels show."""
        lin = min(1.0, max(0.0, (db + 72.0) / 72.0))
        return lin ** 0.8

    def paintEvent(self, _event):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        r = QRectF(self.rect()).adjusted(0.5, 0.5, -0.5, -0.5)

        p.setPen(QColor(_PANEL_EDGE))
        p.setBrush(QColor(_PANEL))
        p.drawRoundedRect(r, 10, 10)

        inset = r.adjusted(10, 8, -10, -8)
        mid = inset.center().y()
        half = inset.height() / 2.0
        step = self._BAR_W + self._GAP

        # Newest sample hugs the right edge; history scrolls left.
        p.setPen(Qt.NoPen)
        cols = int(inset.width() // step)
        samples = list(self._history)[-cols:]
        x = inset.right() - len(samples) * step
        for raw, clean, filtering in samples:
            if filtering:
                h = max(1.0, raw * half)
                p.setBrush(_RAW_BAR)
                p.drawRect(QRectF(x, mid - h, self._BAR_W, 2 * h))
                h = max(1.0, clean * half)
                p.setBrush(_CLEAN_BAR)
                p.drawRect(QRectF(x, mid - h, self._BAR_W, 2 * h))
            else:
                h = max(1.0, raw * half)
                p.setBrush(_IDLE_BAR)
                p.drawRect(QRectF(x, mid - h, self._BAR_W, 2 * h))
            x += step

        pen = QPen(QColor("#3a4152"))
        pen.setStyle(Qt.DotLine)
        p.setPen(pen)
        p.drawLine(inset.left(), mid, inset.right(), mid)


class ToggleSwitch(QCheckBox):
    """A flat on/off switch: a rounded track with a sliding circular knob.

    Custom-painted (no native indicator, no gradients) so it reads clearly as a
    switch rather than a checkbox. Still a QCheckBox underneath, so it keeps the
    ``toggled`` signal, ``isChecked`` and enable/disable the rest of the UI wires
    to. The whole widget is the hit target.
    """

    def __init__(self):
        super().__init__()
        self.setFixedSize(46, 26)
        self.setCursor(Qt.PointingHandCursor)
        # Knob position, 0.0 (off) .. 1.0 (on). Animated on toggle so the knob
        # glides instead of snapping; paintEvent renders from this, not isChecked.
        self._pos = 1.0 if self.isChecked() else 0.0
        self._anim = QPropertyAnimation(self, b"position", self)
        self._anim.setDuration(200)
        self._anim.setEasingCurve(QEasingCurve.InOutCubic)
        self.toggled.connect(self._animate)

    def hitButton(self, pos):
        return self.rect().contains(pos)

    def _animate(self, checked):
        self._anim.stop()
        self._anim.setStartValue(self._pos)
        self._anim.setEndValue(1.0 if checked else 0.0)
        self._anim.start()

    def get_position(self):
        return self._pos

    def set_position(self, value):
        self._pos = value
        self.update()  # repaint each animation tick

    position = Property(float, get_position, set_position)

    def paintEvent(self, _event):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        r = QRectF(self.rect())
        pos = self._pos if self.isEnabled() else 0.0
        p.setPen(Qt.NoPen)
        p.setBrush(QColor(_ACCENT) if pos > 0.5 else QColor("#2c3242"))
        p.drawRoundedRect(r, r.height() / 2, r.height() / 2)
        margin = 3.0
        d = r.height() - 2 * margin
        x = margin + (r.width() - d - 2 * margin) * pos
        p.setBrush(QColor("#f2f3f7") if self.isEnabled() else QColor("#6b7080"))
        p.drawEllipse(QRectF(x, margin, d, d))


def _make_glow(button):
    """A soft accent-colored halo behind the hero button that slowly breathes
    while filtering is on. Driven by animating the drop shadow's blur radius;
    call the returned setter with True/False as the hero toggles."""
    glow = QGraphicsDropShadowEffect(button)
    glow.setColor(QColor(61, 139, 212, 160))
    glow.setOffset(0, 0)
    glow.setBlurRadius(0)
    button.setGraphicsEffect(glow)

    breath = QPropertyAnimation(glow, b"blurRadius", button)
    breath.setDuration(2400)
    breath.setStartValue(22)
    breath.setKeyValueAt(0.5, 46)
    breath.setEndValue(22)
    breath.setEasingCurve(QEasingCurve.InOutSine)
    breath.setLoopCount(-1)

    def set_on(on):
        if on:
            breath.start()
        else:
            breath.stop()
            glow.setBlurRadius(0)

    return set_on


def _preselect_index(devices, current_name):
    """Best dropdown row for the engine's current device.

    The engine may have opened the mic via a different host API (e.g. MME),
    whose names are truncated to 31 chars -- match on the shared prefix.
    """
    prefix = current_name[:28].lower()
    for row, (_, name) in enumerate(devices):
        if name.lower().startswith(prefix) or prefix.startswith(name.lower()):
            return row
    return 0


def _row(*widgets):
    """A horizontal line: label on the left, control(s) on the right."""
    box = QHBoxLayout()
    box.setContentsMargins(0, 0, 0, 0)
    for i, w in enumerate(widgets):
        box.addWidget(w)
        if i == 0:
            box.addStretch(1)
    return box


def _reading(text):
    """A right-hand value label that won't clip as its text grows/shrinks.

    Right-aligned with a minimum width so a widening reading (e.g. "0 dB" ->
    "-52 dB", "gate: shut" -> "gate: OPEN") hugs the right margin instead of
    overflowing past it.
    """
    lbl = QLabel(text)
    lbl.setAlignment(Qt.AlignRight | Qt.AlignVCenter)
    lbl.setMinimumWidth(84)
    return lbl


def _field(caption_text, widget):
    """A tiny uppercase caption stacked above a pill picker."""
    col = QVBoxLayout()
    col.setContentsMargins(0, 0, 0, 0)
    col.setSpacing(4)
    cap = QLabel(caption_text)
    cap.setProperty("class", "fieldcap")
    col.addWidget(cap)
    col.addWidget(widget)
    return col


def run_ui(engine):
    devices = audio.list_input_devices()
    current_name = sd.query_devices(engine.input_index)["name"]

    app = QApplication.instance() or QApplication([])
    app.setStyle("Fusion")  # flat base; no native gradient shading on controls
    root = QWidget()
    root.setObjectName("root")
    root.setWindowTitle("Prism")
    root.setFixedWidth(440)
    root.setStyleSheet(_QSS)

    outer = QVBoxLayout(root)
    outer.setContentsMargins(20, 16, 20, 20)
    outer.setSpacing(12)

    # --- Title with a prism-spectrum underline --------------------------------
    title = QLabel("PRISM")
    title.setObjectName("title")
    spectrum = QFrame()
    spectrum.setObjectName("spectrum")
    spectrum.setFixedWidth(64)
    title_col = QVBoxLayout()
    title_col.setContentsMargins(0, 0, 0, 0)
    title_col.setSpacing(3)
    title_col.addWidget(title)
    title_col.addWidget(spectrum)
    title_row = QHBoxLayout()
    title_row.addLayout(title_col)
    title_row.addStretch(1)
    outer.addLayout(title_row)

    # --- Top pickers: Model | Microphone (two pills side by side) -------------
    model_combo = QComboBox()
    model_combo.addItems([label for label, _ in _DENOISER_OPTIONS])
    model_combo.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)

    mic_combo = QComboBox()
    mic_combo.addItems([name for _, name in devices])
    mic_combo.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
    if devices:
        mic_combo.setCurrentIndex(_preselect_index(devices, current_name))

    pickers = QHBoxLayout()
    pickers.setSpacing(12)
    pickers.addLayout(_field("MODEL", model_combo), 1)
    pickers.addLayout(_field("MICROPHONE", mic_combo), 1)
    outer.addLayout(pickers)

    # --- Live scope + legend ---------------------------------------------------
    scope = LevelScope()
    outer.addWidget(scope)

    legend = QLabel(
        f'<span style="color:#969caf;">&#9632;</span> raw mic&nbsp;&nbsp;'
        f'<span style="color:{_ACCENT};">&#9632;</span> cleaned'
    )
    legend.setProperty("class", "legend")
    reduction_val = QLabel("")
    reduction_val.setProperty("class", "legend")
    reduction_val.setAlignment(Qt.AlignRight | Qt.AlignVCenter)
    outer.addLayout(_row(legend, reduction_val))

    # --- Hero power button ----------------------------------------------------
    hero = QPushButton("⏻")  # power symbol
    hero.setObjectName("hero")
    hero.setCheckable(True)
    hero.setChecked(engine.enabled)
    hero.setFixedSize(104, 104)
    hero.setCursor(Qt.PointingHandCursor)
    set_glow = _make_glow(hero)

    hero_row = QHBoxLayout()
    hero_row.addStretch(1)
    hero_row.addWidget(hero)
    hero_row.addStretch(1)
    outer.addSpacing(4)
    outer.addLayout(hero_row)

    hero_state = QLabel()
    hero_state.setObjectName("heroState")
    hero_state.setAlignment(Qt.AlignCenter)
    outer.addWidget(hero_state)

    status = QLabel()
    status.setObjectName("status")
    status.setAlignment(Qt.AlignCenter)
    outer.addWidget(status)

    def describe():
        on = engine.enabled
        hero_state.setText("FILTERING ON" if on else "PASSTHROUGH")
        hero_state.setStyleSheet(
            f"color: {_ACCENT_BRIGHT};" if on else "color: #8b90a0;")
        set_glow(on)
        # The status line stays empty in the normal case; it only surfaces
        # transient messages (switching a model, a device error).
        status.setText("")

    # --- Settings divider -----------------------------------------------------
    outer.addSpacing(8)
    divider = QLabel("SETTINGS")
    divider.setObjectName("divider")
    divider.setAlignment(Qt.AlignCenter)
    outer.addWidget(divider)
    line = QFrame()
    line.setObjectName("hline")
    line.setFrameShape(QFrame.HLine)
    outer.addWidget(line)
    outer.addSpacing(4)

    # --- AI noise removal: toggle ---------------------------------------------
    denoise_check = ToggleSwitch()
    denoise_check.setChecked(engine.denoiser_enabled)
    denoise_cap = QLabel("AI noise removal")
    outer.addLayout(_row(denoise_cap, denoise_check))

    # --- Strength slider ------------------------------------------------------
    strength = QSlider(Qt.Horizontal)
    strength.setRange(0, 100)
    strength.setValue(int(round(engine.denoiser_mix * 100.0)))
    strength_cap = QLabel("Strength")
    strength_cap.setProperty("class", "muted")
    strength_val = _reading(f"{int(round(engine.denoiser_mix * 100.0))}%")
    strength_val.setProperty("class", "reading")
    outer.addLayout(_row(strength_cap, strength_val))
    outer.addWidget(strength)

    # --- Readings: room noise + gate --------------------------------------------
    floor_cap = QLabel("Room noise")
    floor_cap.setProperty("class", "muted")
    floor_val = _reading("-- dB")
    floor_val.setProperty("class", "reading")
    outer.addLayout(_row(floor_cap, floor_val))

    gate_cap = QLabel("Noise gate")
    gate_cap.setProperty("class", "muted")
    gate_val = _reading("")
    outer.addLayout(_row(gate_cap, gate_val))

    # --- Handlers -------------------------------------------------------------
    def _active_model_label():
        return _NAME_TO_LABEL.get(engine.denoiser_name, "RNNoise")

    def on_hero():
        engine.enabled = hero.isChecked()
        describe()

    def on_denoise_toggle():
        engine.denoiser_enabled = denoise_check.isChecked()

    def on_strength(value):
        engine.denoiser_mix = value / 100.0
        strength_val.setText(f"{value}%")

    def on_model_selected(_index):
        label = model_combo.currentText()
        status.setText(f"Switching to {label}…")
        QApplication.processEvents()  # paint status before the (blocking) load
        try:
            engine.set_denoiser(dict(_DENOISER_OPTIONS)[label])
        except Exception as exc:  # device gone/busy on restart: report, don't crash
            status.setText(f"Denoiser error: {exc}")
            return
        # Reflect what actually loaded -- DeepFilterNet may have fallen back.
        _set_combo(model_combo, _active_model_label())
        if _active_model_label() != label:
            status.setText(f"{label} unavailable — using {_active_model_label()}")
        else:
            describe()

    def on_mic_selected(row):
        index = devices[row][0]
        try:
            engine.switch_input(index)
            describe()
        except Exception as exc:  # device gone/busy: report, don't crash
            status.setText(f"Mic error: {exc}")

    hero.clicked.connect(on_hero)
    denoise_check.toggled.connect(on_denoise_toggle)
    strength.valueChanged.connect(on_strength)
    model_combo.currentIndexChanged.connect(on_model_selected)
    mic_combo.activated.connect(on_mic_selected)
    _set_combo(model_combo, _active_model_label())

    if not engine.denoiser_available:
        denoise_check.setChecked(False)
        denoise_check.setEnabled(False)
        model_combo.setEnabled(False)
        strength.setEnabled(False)

    describe()

    # --- Poll loop (display-only reads; never blocks audio) -------------------
    filtering_active = (lambda: engine.enabled and engine.denoiser_enabled
                        and engine.denoiser_available)

    def poll():
        scope.push(engine.in_db, engine.out_db, filtering_active())
        floor_val.setText(f"{engine.noise_floor_db:.0f} dB")
        if filtering_active() and engine.reduction_db >= 0.5:
            reduction_val.setText(
                f'<span style="color:{_ACCENT_BRIGHT};">'
                f"−{engine.reduction_db:.0f} dB noise removed</span>")
        else:
            reduction_val.setText("")
        if engine.enabled and engine.gate_open:
            gate_val.setText(f'<span style="color:{_ACCENT_BRIGHT};">'
                             f"&#9679;</span> open")
        elif engine.enabled:
            gate_val.setText('<span style="color:#5a5f70;">&#9679;</span> shut')
        else:
            gate_val.setText('<span style="color:#5a5f70;">&#9679;</span> bypassed')

    timer = QTimer(root)
    timer.timeout.connect(poll)
    timer.start(_POLL_MS)

    # Closing the window quits the app (quitOnLastWindowClosed); stop the stream
    # as the loop exits. aboutToQuit fires reliably where an instance-level
    # closeEvent override on a plain QWidget would not.
    app.aboutToQuit.connect(engine.stop)
    root.show()
    app.exec()


def _set_combo(combo, text):
    """Set a combobox to `text` without firing its change handler."""
    combo.blockSignals(True)
    idx = combo.findText(text)
    if idx >= 0:
        combo.setCurrentIndex(idx)
    combo.blockSignals(False)
