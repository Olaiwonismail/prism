"""Minimal Tkinter control window (stopgap before the Tauri UI).

One window: noise-filtering toggle, mic picker, live level meter with gate
state, and a status line. The Tk mainloop owns the main thread; audio runs on
PortAudio's callback thread inside AudioEngine.
"""

import tkinter as tk
from tkinter import ttk

import sounddevice as sd

from . import audio

_DB_FLOOR = -60.0  # quietest level the meter shows
_POLL_MS = 50      # UI refresh interval (~20 fps)


def _preselect_index(devices, current_name):
    """Best dropdown row for the engine's current device.

    The engine may have opened the mic via a different host API (e.g. MME),
    whose names are truncated to 31 chars — match on the shared prefix.
    """
    prefix = current_name[:28].lower()
    for row, (_, name) in enumerate(devices):
        if name.lower().startswith(prefix) or prefix.startswith(name.lower()):
            return row
    return 0


def run_ui(engine):
    devices = audio.list_input_devices()
    current_name = sd.query_devices(engine.input_index)["name"]

    root = tk.Tk()
    root.title("Prism")
    root.resizable(False, False)
    main = ttk.Frame(root, padding=12)
    main.grid(sticky="nsew")

    # --- Noise filtering toggle ---------------------------------------------
    enabled_var = tk.BooleanVar(value=engine.enabled)
    status_var = tk.StringVar()

    def describe():
        mode = "filtering" if engine.enabled else "passthrough (no filtering)"
        status_var.set(f"Running — {mode} → CABLE Input")

    def on_toggle():
        engine.enabled = enabled_var.get()
        describe()

    ttk.Checkbutton(
        main, text="Noise filtering", variable=enabled_var, command=on_toggle
    ).grid(row=0, column=0, columnspan=2, sticky="w")

    # --- AI noise removal toggle ----------------------------------------------
    rnnoise_var = tk.BooleanVar(value=engine.rnnoise_enabled)

    def on_rnnoise_toggle():
        engine.rnnoise_enabled = rnnoise_var.get()

    rnnoise_check = ttk.Checkbutton(
        main, text="AI noise removal (RNNoise)",
        variable=rnnoise_var, command=on_rnnoise_toggle,
    )
    rnnoise_check.grid(row=1, column=0, columnspan=2, sticky="w")
    if not engine.rnnoise_available:
        rnnoise_var.set(False)
        rnnoise_check.state(["disabled"])

    # --- Mic picker -----------------------------------------------------------
    ttk.Label(main, text="Microphone:").grid(row=2, column=0, sticky="w", pady=(10, 0))
    mic_combo = ttk.Combobox(
        main, values=[name for _, name in devices], state="readonly", width=42
    )
    mic_combo.grid(row=3, column=0, columnspan=2, sticky="we")
    if devices:
        mic_combo.current(_preselect_index(devices, current_name))

    def on_mic_selected(_event):
        index = devices[mic_combo.current()][0]
        try:
            engine.switch_input(index)
            describe()
        except Exception as exc:  # device gone/busy: report, don't crash
            status_var.set(f"Mic error: {exc}")

    mic_combo.bind("<<ComboboxSelected>>", on_mic_selected)

    # --- Level meter ----------------------------------------------------------
    ttk.Label(main, text="Input level:").grid(row=4, column=0, sticky="w", pady=(10, 0))
    gate_var = tk.StringVar(value="gate: shut")
    ttk.Label(main, textvariable=gate_var).grid(row=4, column=1, sticky="e", pady=(10, 0))
    meter = ttk.Progressbar(main, length=300, maximum=-_DB_FLOOR)
    meter.grid(row=5, column=0, columnspan=2, sticky="we")

    # --- Status line ----------------------------------------------------------
    ttk.Label(main, textvariable=status_var).grid(
        row=6, column=0, columnspan=2, sticky="w", pady=(10, 0)
    )
    describe()

    def poll():
        meter["value"] = max(0.0, engine.in_db - _DB_FLOOR)
        gate_var.set("gate: OPEN" if engine.gate_open else "gate: shut")
        root.after(_POLL_MS, poll)

    def on_close():
        engine.stop()
        root.destroy()

    root.protocol("WM_DELETE_WINDOW", on_close)
    poll()
    root.mainloop()
