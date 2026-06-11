"""Live input level meter for tuning the noise gate.

Run:  ./venv/Scripts/python.exe -m tools.level_meter

Shows your mic's loudness in dBFS in real time. Use it to set
NOISE_GATE_THRESHOLD_DB in prism/config.py:

  1. Stay silent (let only the background noise play) -> note the dB it hovers at,
     e.g. -28 dB. That's your NOISE FLOOR.
  2. Speak normally -> note the dB your voice reaches, e.g. -12 dB.
  3. Set the threshold a few dB ABOVE the noise floor but BELOW your voice,
     e.g. -22. Noise stays gated out; your voice opens the gate.

Press Ctrl+C to stop.
"""

import numpy as np
import sounddevice as sd

from prism import config

_INT16_SCALE = 32768.0
_BAR_WIDTH = 40
_DB_FLOOR = -60.0  # quietest level the bar shows


def _dbfs(block_i16):
    x = block_i16.astype(np.float32) / _INT16_SCALE
    rms = float(np.sqrt(np.mean(x * x)))
    if rms <= 0.0:
        return _DB_FLOOR
    return max(_DB_FLOOR, 20.0 * np.log10(rms))


def _callback(indata, frames, time, status):
    if status:
        print("Audio status:", status)
    db = _dbfs(indata[:, 0])
    # Map [_DB_FLOOR, 0] dB onto the bar width.
    filled = int(round((db - _DB_FLOOR) / (-_DB_FLOOR) * _BAR_WIDTH))
    filled = max(0, min(_BAR_WIDTH, filled))
    bar = "#" * filled + "-" * (_BAR_WIDTH - filled)
    gate = "OPEN " if db >= config.NOISE_GATE_THRESHOLD_DB else "shut "
    # \r keeps it on one updating line.
    print(f"\r[{bar}] {db:6.1f} dBFS  gate:{gate}"
          f"(threshold {config.NOISE_GATE_THRESHOLD_DB:.0f})", end="", flush=True)


def main():
    input_index = sd.default.device[0]
    print(f"Mic: [{input_index}] {sd.query_devices(input_index)['name']}")
    print("Silence = noise floor, speak = voice level. Ctrl+C to stop.\n")
    with sd.InputStream(
        device=input_index,
        samplerate=config.SAMPLERATE,
        blocksize=config.BLOCKSIZE,
        dtype=config.DTYPE,
        channels=1,
        callback=_callback,
    ):
        try:
            while True:
                sd.sleep(1000)
        except KeyboardInterrupt:
            print("\nStopped.")


if __name__ == "__main__":
    main()
