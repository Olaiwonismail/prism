# Prism entry point: physical mic -> DSP pipeline -> virtual audio device.
#
# Other apps (Zoom, Discord, OBS, browser, ...) select the virtual microphone
# ("CABLE Output" on Windows, "Prism Microphone" on macOS/Linux) as their mic
# to receive Prism's processed audio.

import sys

import sounddevice as sd

from prism import audio, bootstrap, config
from prism.ui_qt import run_ui

WINDOWS_INSTALL_HELP = """\
Could not find the VB-Audio Virtual Cable output device ("CABLE Input").

Install it (one-time):
  1. Download VB-CABLE from https://vb-audio.com/Cable/
  2. Unzip, right-click VBCABLE_Setup_x64.exe -> Run as administrator
  3. Click "Install Driver", then reboot
  4. Confirm "CABLE Input" appears under Windows Sound -> Playback
"""

MAC_INSTALL_HELP = """\
Could not find (or install) the "Prism Microphone" virtual device.

Install it (one-time):
  1. Build the driver: bash mac/build_driver.sh (or grab the PrismAudio.driver
     CI artifact from GitHub Actions)
  2. sudo cp -R PrismAudio.driver /Library/Audio/Plug-Ins/HAL/
  3. sudo killall coreaudiod
  4. Confirm "Prism Microphone" appears in System Settings -> Sound -> Input
"""

LINUX_INSTALL_HELP = """\
Could not create the Prism virtual microphone.

Prism uses PipeWire's pw-loopback to create it at runtime (no install).
Check that PipeWire is running (most modern distros ship it), then start
Prism again.
"""

NO_MIC_HELP = """\
Could not find a usable microphone.

Prism needs a real mic as its input (it never uses its own virtual device --
that would make it listen to its own output). Plug in a microphone, or check
that one is enabled in your sound settings, then run Prism again.
"""


def _install_help():
    if sys.platform == "darwin":
        return MAC_INSTALL_HELP
    if sys.platform.startswith("linux"):
        return LINUX_INSTALL_HELP
    return WINDOWS_INSTALL_HELP


def _fail(message):
    """Report a fatal startup problem and exit.

    The packaged .exe runs windowed (no console), so the message also goes to
    a dialog there -- otherwise the app would just silently not appear.
    """
    print(message, file=sys.stderr)
    if getattr(sys, "frozen", False):
        import tkinter as tk
        from tkinter import messagebox
        root = tk.Tk()
        root.withdraw()
        messagebox.showerror("Prism", message)
    sys.exit(1)


def _ask_setup(message):
    """Yes/no prompt for the one-time device install (dialog when windowed)."""
    if getattr(sys, "frozen", False):
        import tkinter as tk
        from tkinter import messagebox
        root = tk.Tk()
        root.withdraw()
        answer = messagebox.askyesno("Prism", message)
        root.destroy()
        return answer
    reply = input(f"{message} [y/N] ").strip().lower()
    return reply in ("y", "yes")


def main():
    result = bootstrap.ensure_virtual_device(ask=_ask_setup)
    if not result.ok:
        _fail(result.message or _install_help())

    input_index = audio.pick_input_device()
    if input_index is None:
        _fail(NO_MIC_HELP)

    print(f"Mic input : [{input_index}] {sd.query_devices(input_index)['name']}")
    if result.output_index is not None:
        name = sd.query_devices(result.output_index)["name"]
        print(f"Output    : [{result.output_index}] {name}")
    else:  # macOS: no output device; the engine feeds the HAL driver's ring
        print(f"Output    : {config.MAC_VIRTUAL_MIC_NAME} (shared-memory ring)")

    engine = audio.AudioEngine(result.output_index)
    engine.start(input_index)
    run_ui(engine)

if __name__ == "__main__":
    main()
