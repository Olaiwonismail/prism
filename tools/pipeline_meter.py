"""Diagnostic meter: shows input vs. output level through the real pipeline.

Run:  ./venv/Scripts/python.exe -m tools.pipeline_meter

Runs the default pipeline (high-pass -> noise gate) on live mic audio and prints
the loudness BEFORE and AFTER processing, plus the gate state. Use it to verify
the gate is actually silencing the gaps:

  - Stay silent: OUT should drop far below IN (e.g. in -35 dB -> out -60 dB).
    If OUT stays near IN, the gate isn't closing -> real bug to fix.
  - Speak: IN and OUT should track each other (gate open).

Press Ctrl+C to stop.
"""

import numpy as np
import sounddevice as sd

from prism import config
from prism.pipeline import build_default_pipeline

_INT16_SCALE = 32768.0
_DB_FLOOR = -80.0


def _dbfs(samples_float):
    rms = float(np.sqrt(np.mean(samples_float * samples_float)))
    if rms <= 0.0:
        return _DB_FLOOR
    return max(_DB_FLOOR, 20.0 * np.log10(rms))


def main():
    pipeline = build_default_pipeline()
    input_index = sd.default.device[0]
    print(f"Mic: [{input_index}] {sd.query_devices(input_index)['name']}")
    print(f"Gate threshold: {config.NOISE_GATE_THRESHOLD_DB:.0f} dBFS")
    print("Silence -> OUT should fall well below IN. Ctrl+C to stop.\n")

    def callback(indata, frames, time, status):
        if status:
            print("Audio status:", status)
        in_db = _dbfs(indata[:, 0].astype(np.float32) / _INT16_SCALE)
        out_i16 = pipeline.process_int16(indata)
        out_db = _dbfs(out_i16.astype(np.float32) / _INT16_SCALE)
        gate = "OPEN" if in_db >= config.NOISE_GATE_THRESHOLD_DB else "shut"
        drop = out_db - in_db
        print(f"\rIN {in_db:6.1f}  ->  OUT {out_db:6.1f} dBFS "
              f"(drop {drop:6.1f} dB)  gate:{gate}  ", end="", flush=True)

    with sd.InputStream(
        device=input_index,
        samplerate=config.SAMPLERATE,
        blocksize=config.BLOCKSIZE,
        dtype=config.DTYPE,
        channels=1,
        callback=callback,
    ):
        try:
            while True:
                sd.sleep(1000)
        except KeyboardInterrupt:
            print("\nStopped.")


if __name__ == "__main__":
    main()
