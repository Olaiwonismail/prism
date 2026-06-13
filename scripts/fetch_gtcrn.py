"""Download the GTCRN ONNX model the "gtcrn" denoiser uses.

GTCRN's streaming export is a tiny ~0.5 MB ONNX file. This fetches the
simplified streaming model straight from the upstream repo into models/gtcrn/.
Run it once:

    ./venv/Scripts/python.exe scripts/fetch_gtcrn.py

Then set DENOISER = "gtcrn" in prism/config.py. Uses only the standard library
so it works before onnxruntime is installed.
"""

import urllib.request
from pathlib import Path

URL = ("https://raw.githubusercontent.com/Xiaobin-Rong/gtcrn/"
       "main/stream/onnx_models/gtcrn_simple.onnx")
DEST = (Path(__file__).resolve().parents[1]
        / "models" / "gtcrn" / "gtcrn_simple.onnx")


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
    print('Done. Set DENOISER = "gtcrn" in prism/config.py to use it.')


if __name__ == "__main__":
    main()
