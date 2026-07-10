"""Download the Silero VAD ONNX model the speech gate uses.

Silero VAD is a tiny (~2.3 MB) MIT-licensed voice activity detector. This
fetches the streaming ONNX model straight from the upstream repo into
models/silero_vad/. Run it once:

    ./venv/Scripts/python.exe scripts/fetch_silero_vad.py

Then set GATE_MODE = "vad" in prism/config.py. Uses only the standard library
so it works before onnxruntime is installed.
"""

import urllib.request
from pathlib import Path

URL = ("https://raw.githubusercontent.com/snakers4/silero-vad/"
       "master/src/silero_vad/data/silero_vad.onnx")
DEST = (Path(__file__).resolve().parents[1]
        / "models" / "silero_vad" / "silero_vad.onnx")


def main():
    if DEST.exists():
        print(f"Already present: {DEST}")
        return
    DEST.parent.mkdir(parents=True, exist_ok=True)
    print(f"Downloading {URL} ...")
    with urllib.request.urlopen(URL) as resp:
        data = resp.read()
    DEST.write_bytes(data)
    print(f"Wrote {DEST} ({DEST.stat().st_size / 1e6:.1f} MB)")
    print('Done. Set GATE_MODE = "vad" in prism/config.py to use it.')


if __name__ == "__main__":
    main()
