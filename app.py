# Prism entry point: physical mic -> DSP pipeline -> VB-Audio Virtual Cable.
#
# Other apps (Zoom, Discord, OBS, browser, ...) select "CABLE Output" as their
# microphone to receive Prism's processed audio.

import sys

import sounddevice as sd

from prism import audio, config
from prism.ui import run_ui

INSTALL_HELP = """\
Could not find the VB-Audio Virtual Cable output device ("CABLE Input").

Install it (one-time):
  1. Download VB-CABLE from https://vb-audio.com/Cable/
  2. Unzip, right-click VBCABLE_Setup_x64.exe -> Run as administrator
  3. Click "Install Driver", then reboot
  4. Confirm "CABLE Input" appears under Windows Sound -> Playback
"""

NO_MIC_HELP = """\
Could not find a usable microphone.

Prism needs a real mic as its input (it never uses "CABLE Output" -- that would
make it listen to its own output). Plug in a microphone, or check that one is
enabled under Windows Sound -> Recording, then run Prism again.
"""


def main():
    cable_index = audio.find_device(config.CABLE_NAME, "output")
    if cable_index is None:
        print(INSTALL_HELP, file=sys.stderr)
        sys.exit(1)

    input_index = audio.pick_input_device()
    if input_index is None:
        print(NO_MIC_HELP, file=sys.stderr)
        sys.exit(1)

    print(f"Mic input : [{input_index}] {sd.query_devices(input_index)['name']}")
    print(f"Output    : [{cable_index}] {sd.query_devices(cable_index)['name']}")

    engine = audio.AudioEngine(cable_index)
    engine.start(input_index)
    run_ui(engine)

if __name__ == "__main__":
    main()
