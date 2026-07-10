"""PySide6 control window (replaces the Tkinter stopgap).

Top of the window mirrors a two-pill layout: a Model picker and a Microphone
picker side by side, with a live scope below them: a scrolling waveform that
draws the raw mic level in grey and the cleaned output level in green on top,
so the grey visible above the green is the noise being stripped in real time.
Under that sit the hero power toggle and the noise-removal strength slider.

Qt's event loop owns the main thread; audio runs on PortAudio's callback thread
inside AudioEngine. A QTimer polls display-only ``engine.*`` values at ~30 fps --
it only *reads* pre-computed numbers, so it never touches the audio callback or
does DSP.
"""

import sys
from collections import deque

from PySide6.QtCore import QPointF, QRectF, Qt, QTimer
from PySide6.QtGui import (
    QBrush, QColor, QIcon, QLinearGradient, QPainter, QPen, QPixmap,
    QPolygonF, QRadialGradient,
)
from PySide6.QtWidgets import (
    QApplication, QComboBox, QFrame, QHBoxLayout, QLabel, QPushButton,
    QSizePolicy, QSlider, QVBoxLayout, QWidget,
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
QLabel[class="fieldcap"] { color: #8b90a0; font-size: 11px; font-weight: 600; }
QLabel[class="muted"] { color: #8b90a0; }
QLabel[class="reading"] { color: #cfd3de; }
QLabel[class="legend"] { color: #868c9e; font-size: 11px; }
QLabel:disabled { color: #565b6a; }

QFrame#card {
    background: #131722; border: 1px solid #232838; border-radius: 12px;
}

/* Pill-shaped pickers (the two dropdowns up top share this look). */
QComboBox {
    background: #1c2030; border: 1px solid #2c3242; border-radius: 18px;
    padding: 9px 16px; min-height: 18px;
}
QComboBox:hover { border-color: #3a4152; }
QComboBox:focus { border-color: #3d8bd4; }
QComboBox::drop-down { border: none; width: 26px; }
/* Blank the native arrow; PillCombo paints its own chevron. */
QComboBox::down-arrow { image: none; width: 0; height: 0; border: none; }
QComboBox QAbstractItemView {
    background: #1c2030; border: 1px solid #2c3242; border-radius: 6px;
    selection-background-color: #3d8bd4; outline: none;
}

QSlider::groove:horizontal { height: 4px; background: #2c3242; border-radius: 2px; }
QSlider::sub-page:horizontal { background: #3d8bd4; border-radius: 2px; }
QSlider::handle:horizontal {
    width: 16px; margin: -7px 0; border-radius: 8px; background: #e6e8ee;
}
QSlider::handle:horizontal:focus { background: #5fa8e8; }
QSlider::sub-page:horizontal:disabled { background: #3a4152; }
QSlider::handle:horizontal:disabled { background: #6b7080; }
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


class PillCombo(QComboBox):
    """A QComboBox that paints its own dropdown chevron.

    Qt's QSS has no reliable way to draw an arrow without an image asset
    (the CSS border-triangle trick renders as a filled rectangle), so the
    pill look comes from the stylesheet and the chevron from QPainter.
    """

    def paintEvent(self, event):
        super().paintEvent(event)
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        p.setPen(QPen(QColor("#8b90a0"), 1.6, Qt.SolidLine,
                      Qt.RoundCap, Qt.RoundJoin))
        cx = self.width() - 19.0
        cy = self.height() / 2.0
        p.drawPolyline(QPolygonF([QPointF(cx - 4, cy - 2),
                                  QPointF(cx, cy + 2.5),
                                  QPointF(cx + 4, cy - 2)]))


class PowerButton(QPushButton):
    """The hero toggle: a circular button with a painted power symbol.

    Everything is drawn with QPainter, deliberately: the power glyph U+23FB
    exists in no font on a stock Windows install, so a text "⏻" rendered as a
    tofu box *and* stalled the window's first show() for seconds while Qt
    scanned every installed font for a fallback (measured ~3s; it starved all
    timers, freezing the scope and animations).
    """

    _FACE = 104.0  # diameter of the button face

    def __init__(self):
        super().__init__()
        self.setCheckable(True)
        self.setFixedSize(int(self._FACE), int(self._FACE))
        self.setCursor(Qt.PointingHandCursor)
        self.toggled.connect(self.update)

    def enterEvent(self, event):
        super().enterEvent(event)
        self.update()  # repaint for the hover border

    def leaveEvent(self, event):
        super().leaveEvent(event)
        self.update()

    def paintEvent(self, _event):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        center = QRectF(self.rect()).center()
        face_r = self._FACE / 2.0 - 1.0  # keep the border stroke inside
        on = self.isChecked()

        face = QRectF(center.x() - face_r, center.y() - face_r,
                      2 * face_r, 2 * face_r)
        if on:
            fill = QRadialGradient(center, face_r)
            fill.setColorAt(0.0, QColor("#16334f"))
            fill.setColorAt(1.0, QColor("#10243a"))
            p.setBrush(fill)
            p.setPen(QPen(QColor(_ACCENT), 2))
        else:
            p.setBrush(QColor("#1a1f2e"))
            hover = self.underMouse()
            p.setPen(QPen(QColor("#3a4152" if hover else "#2c3242"), 2))
        p.drawEllipse(face)

        if self.hasFocus():
            ring_r = face_r - 5.0
            p.setBrush(Qt.NoBrush)
            p.setPen(QPen(QColor(_ACCENT_BRIGHT), 1.5))
            p.drawEllipse(center, ring_r, ring_r)

        # Power symbol (IEC 5009): a ring with a gap at the top and a stem
        # dropping through the gap.
        icon_r = 19.0
        color = QColor(_ACCENT_BRIGHT) if on else QColor("#7d8296")
        p.setPen(QPen(color, 3.4, Qt.SolidLine, Qt.RoundCap))
        ring = QRectF(center.x() - icon_r, center.y() - icon_r + 3,
                      2 * icon_r, 2 * icon_r)
        p.drawArc(ring, 125 * 16, 290 * 16)  # gap of 70 deg centered on top
        p.drawLine(QPointF(center.x(), center.y() - icon_r - 4),
                   QPointF(center.x(), center.y() - 1))


def _paint_icon(size):
    """The Prism mark at one pixel size: the docs-site logo (a triangle with a
    teal->purple->pink gradient stroke) on the app's dark rounded tile."""
    pm = QPixmap(size, size)
    pm.fill(Qt.transparent)
    p = QPainter(pm)
    p.setRenderHint(QPainter.Antialiasing)
    s = size / 48.0  # geometry designed on a 48px grid

    p.setPen(Qt.NoPen)
    p.setBrush(QColor("#161a26"))
    p.drawRoundedRect(QRectF(0, 0, size, size), 11 * s, 11 * s)

    grad = QLinearGradient(0, 0, size, size)
    grad.setColorAt(0.0, QColor("#6fc9d8"))
    grad.setColorAt(0.55, QColor("#9b90d4"))
    grad.setColorAt(1.0, QColor("#d3a3d9"))
    pen = QPen(QBrush(grad), max(1.6, 3.2 * s))
    pen.setJoinStyle(Qt.RoundJoin)
    p.setPen(pen)
    p.setBrush(Qt.NoBrush)
    p.drawPolygon(QPolygonF([QPointF(24 * s, 10 * s),
                             QPointF(40 * s, 37 * s),
                             QPointF(8 * s, 37 * s)]))
    p.end()
    return pm


def _app_icon():
    """Window/taskbar icon painted in code -- no image asset to ship or load."""
    icon = QIcon()
    for size in (16, 20, 24, 32, 48, 64, 128, 256):
        icon.addPixmap(_paint_icon(size))
    return icon


def _taskbar_identity():
    """Give the process its own Windows taskbar identity.

    Python GUI apps inherit python.exe's AppUserModelID, so without this the
    taskbar groups Prism under Python and shows the Python icon instead of
    the one set via setWindowIcon. No-op off Windows.
    """
    if sys.platform != "win32":
        return
    try:
        import ctypes
        ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID("Prism.Prism")
    except Exception:
        pass


def _dark_titlebar(widget):
    """Ask DWM for a dark window frame so the title bar matches the UI.

    Windows 10 20H1+ / Windows 11 only; a silent no-op anywhere else.
    """
    if sys.platform != "win32":
        return
    try:
        import ctypes
        value = ctypes.c_int(1)
        ctypes.windll.dwmapi.DwmSetWindowAttribute(
            int(widget.winId()), 20,  # DWMWA_USE_IMMERSIVE_DARK_MODE
            ctypes.byref(value), ctypes.sizeof(value))
    except Exception:
        pass


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

    Right-aligned with a minimum width so a widening reading (e.g. "0%" ->
    "100%") hugs the right margin instead of overflowing past it.
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
    _taskbar_identity()
    app.setWindowIcon(_app_icon())
    root = QWidget()
    root.setObjectName("root")
    root.setWindowTitle("Prism")
    root.setFixedWidth(440)
    root.setStyleSheet(_QSS)

    outer = QVBoxLayout(root)
    outer.setContentsMargins(20, 16, 20, 20)
    outer.setSpacing(12)

    # --- Title -----------------------------------------------------------------
    title = QLabel("PRISM")
    title.setObjectName("title")
    title_row = QHBoxLayout()
    title_row.addWidget(title)
    title_row.addStretch(1)
    outer.addLayout(title_row)

    # --- Top pickers: Model | Microphone (two pills side by side) -------------
    model_combo = PillCombo()
    model_combo.addItems([label for label, _ in _DENOISER_OPTIONS])
    model_combo.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)

    mic_combo = PillCombo()
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
    outer.addWidget(legend)

    # --- Hero power button ----------------------------------------------------
    hero = PowerButton()
    hero.setChecked(engine.enabled)

    hero_row = QHBoxLayout()
    hero_row.addStretch(1)
    hero_row.addWidget(hero)
    hero_row.addStretch(1)
    outer.addSpacing(8)
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
        # The status line stays empty in the normal case; it only surfaces
        # transient messages (switching a model, a device error).
        status.setText("")

    # --- Settings card ----------------------------------------------------------
    outer.addSpacing(4)
    card = QFrame()
    card.setObjectName("card")
    card_lay = QVBoxLayout(card)
    card_lay.setContentsMargins(16, 14, 16, 14)
    card_lay.setSpacing(10)
    outer.addWidget(card)

    # Noise-removal strength slider
    strength = QSlider(Qt.Horizontal)
    strength.setRange(0, 100)
    strength.setValue(int(round(engine.denoiser_mix * 100.0)))
    strength_cap = QLabel("Noise removal")
    strength_cap.setProperty("class", "muted")
    strength_val = _reading(f"{int(round(engine.denoiser_mix * 100.0))}%")
    strength_val.setProperty("class", "reading")
    card_lay.addLayout(_row(strength_cap, strength_val))
    card_lay.addWidget(strength)

    # --- Handlers -------------------------------------------------------------
    def _active_model_label():
        return _NAME_TO_LABEL.get(engine.denoiser_name, "RNNoise")

    def on_hero():
        engine.enabled = hero.isChecked()
        describe()

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
    strength.valueChanged.connect(on_strength)
    model_combo.currentIndexChanged.connect(on_model_selected)
    mic_combo.activated.connect(on_mic_selected)
    _set_combo(model_combo, _active_model_label())

    if not engine.denoiser_available:
        model_combo.setEnabled(False)
        for w in (strength, strength_cap, strength_val):
            w.setEnabled(False)

    describe()

    # --- Poll loop (display-only reads; never blocks audio) -------------------
    filtering_active = (lambda: engine.enabled and engine.denoiser_enabled
                        and engine.denoiser_available)

    def poll():
        scope.push(engine.in_db, engine.out_db, filtering_active())

    timer = QTimer(root)
    timer.timeout.connect(poll)
    timer.start(_POLL_MS)

    # Closing the window quits the app (quitOnLastWindowClosed); stop the stream
    # as the loop exits. aboutToQuit fires reliably where an instance-level
    # closeEvent override on a plain QWidget would not.
    app.aboutToQuit.connect(engine.stop)
    _dark_titlebar(root)
    root.show()
    app.exec()


def _set_combo(combo, text):
    """Set a combobox to `text` without firing its change handler."""
    combo.blockSignals(True)
    idx = combo.findText(text)
    if idx >= 0:
        combo.setCurrentIndex(idx)
    combo.blockSignals(False)
