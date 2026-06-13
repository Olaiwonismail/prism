"""Minimal Tkinter control window (stopgap before the Tauri UI).

One window: noise-filtering toggle, mic picker, live level meter with gate
state, and a status line. The Tk mainloop owns the main thread; audio runs on
PortAudio's callback thread inside AudioEngine.
"""

import tkinter as tk
from tkinter import ttk

import sounddevice as sd

from . import audio

_DB_FLOOR = -60.0       # quietest level the meter shows
_POLL_MS = 50           # UI refresh interval (~20 fps)
_REDUCTION_MAX_DB = 40.0  # full-scale of the "noise removed" bar

# Denoiser models offered in the picker: (display label, config.DENOISER value).
_DENOISER_OPTIONS = [
    ("RNNoise", "rnnoise"),
    ("GTCRN", "gtcrn"),
    ("DeepFilterNet", "deepfilternet"),
]
# Map a loaded stage's .name back to its picker label (see _active_model_label).
_NAME_TO_LABEL = {"RNNoise": "RNNoise", "GTCRN": "GTCRN",
                  "DeepFilterNet3": "DeepFilterNet"}


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

    # --- AI noise removal: enable toggle + model picker -----------------------
    denoise_var = tk.BooleanVar(value=engine.denoiser_enabled)

    def on_denoise_toggle():
        engine.denoiser_enabled = denoise_var.get()

    denoise_check = ttk.Checkbutton(
        main, text="AI noise removal", variable=denoise_var,
        command=on_denoise_toggle,
    )
    denoise_check.grid(row=1, column=0, sticky="w")

    model_combo = ttk.Combobox(
        main, values=[label for label, _ in _DENOISER_OPTIONS],
        state="readonly", width=16,
    )
    model_combo.grid(row=1, column=1, sticky="e")

    # --- Noise removal strength ----------------------------------------------
    strength_caption = tk.StringVar()
    ttk.Label(main, text="Strength:").grid(row=2, column=0, sticky="w")
    ttk.Label(main, textvariable=strength_caption).grid(row=2, column=1, sticky="e")

    def on_strength(value):
        engine.denoiser_mix = float(value) / 100.0
        strength_caption.set(f"{int(round(float(value)))}%")

    strength = ttk.Scale(main, from_=0, to=100, orient="horizontal",
                         command=on_strength)
    strength.set(engine.denoiser_mix * 100.0)
    strength.grid(row=3, column=0, columnspan=2, sticky="we")
    strength_caption.set(f"{int(round(engine.denoiser_mix * 100.0))}%")

    def _active_model_label():
        return _NAME_TO_LABEL.get(engine.denoiser_name, "RNNoise")

    def on_model_selected(_event):
        label = model_combo.get()
        status_var.set(f"Switching to {label}…")
        root.update_idletasks()  # render status before the (blocking) model load
        try:
            engine.set_denoiser(dict(_DENOISER_OPTIONS)[label])
        except Exception as exc:  # device gone/busy on restart: report, don't crash
            status_var.set(f"Denoiser error: {exc}")
            return
        # Reflect what actually loaded — DeepFilterNet may have fallen back.
        model_combo.set(_active_model_label())
        if _active_model_label() != label:
            status_var.set(f"{label} unavailable — using {_active_model_label()}")
        else:
            describe()

    model_combo.bind("<<ComboboxSelected>>", on_model_selected)
    model_combo.set(_active_model_label())

    if not engine.denoiser_available:
        denoise_var.set(False)
        denoise_check.state(["disabled"])
        model_combo.state(["disabled"])
        strength.state(["disabled"])

    # --- Mic picker -----------------------------------------------------------
    ttk.Label(main, text="Microphone:").grid(row=4, column=0, sticky="w", pady=(10, 0))
    mic_combo = ttk.Combobox(
        main, values=[name for _, name in devices], state="readonly", width=42
    )
    mic_combo.grid(row=5, column=0, columnspan=2, sticky="we")
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
    ttk.Label(main, text="Input level:").grid(row=6, column=0, sticky="w", pady=(10, 0))
    gate_var = tk.StringVar(value="gate: shut")
    ttk.Label(main, textvariable=gate_var).grid(row=6, column=1, sticky="e", pady=(10, 0))
    meter = ttk.Progressbar(main, length=300, maximum=-_DB_FLOOR)
    meter.grid(row=7, column=0, columnspan=2, sticky="we")

    # --- Noise meter ----------------------------------------------------------
    # Two readings: how noisy the room is, and how much we're stripping out.
    floor_var = tk.StringVar()
    ttk.Label(main, text="Room noise:").grid(row=8, column=0, sticky="w", pady=(10, 0))
    ttk.Label(main, textvariable=floor_var).grid(row=8, column=1, sticky="e", pady=(10, 0))

    removed_var = tk.StringVar()
    ttk.Label(main, text="Noise removed:").grid(row=9, column=0, sticky="w")
    ttk.Label(main, textvariable=removed_var).grid(row=9, column=1, sticky="e")
    removed_bar = ttk.Progressbar(main, length=300, maximum=_REDUCTION_MAX_DB)
    removed_bar.grid(row=10, column=0, columnspan=2, sticky="we")

    # --- Status line ----------------------------------------------------------
    ttk.Label(main, textvariable=status_var).grid(
        row=11, column=0, columnspan=2, sticky="w", pady=(10, 0)
    )
    describe()

    def poll():
        meter["value"] = max(0.0, engine.in_db - _DB_FLOOR)
        gate_var.set("gate: OPEN" if engine.gate_open else "gate: shut")
        floor_var.set(f"{engine.noise_floor_db:.0f} dB")
        removed_var.set(f"{engine.reduction_db:.0f} dB")
        removed_bar["value"] = min(_REDUCTION_MAX_DB, max(0.0, engine.reduction_db))
        root.after(_POLL_MS, poll)

    def on_close():
        engine.stop()
        root.destroy()

    root.protocol("WM_DELETE_WINDOW", on_close)
    poll()
    root.mainloop()
