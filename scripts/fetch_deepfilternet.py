"""Download the DeepFilterNet3 ONNX model the "deepfilternet" denoiser uses.

The model is a 13 MB binary, so it isn't committed to the repo. This fetches the
torch-free streaming export from the grazder/DeepFilterNet torchDF branch and
unpacks just the ONNX file into models/deepfilternet3/. Run it once:

    ./venv/Scripts/python.exe scripts/fetch_deepfilternet.py

Then set DENOISER = "deepfilternet" in prism/config.py. Uses only the standard
library so it works before onnxruntime is installed.
"""

import io
import tarfile
import urllib.request
from pathlib import Path

URL = ("https://raw.githubusercontent.com/grazder/DeepFilterNet/"
       "torchDF_main/models/DeepFilterNet3_torchDF_onnx.tar")
MEMBER = "DeepFilterNet3_torchDF_onnx/denoiser_model.onnx"
DEST = (Path(__file__).resolve().parents[1]
        / "models" / "deepfilternet3" / "denoiser_model.onnx")


def main():
    if DEST.exists():
        print(f"Already present: {DEST}")
        return
    DEST.parent.mkdir(parents=True, exist_ok=True)
    print(f"Downloading {URL} ...")
    with urllib.request.urlopen(URL) as resp:
        data = resp.read()
    print(f"Got {len(data) / 1e6:.1f} MB; extracting the ONNX model ...")
    with tarfile.open(fileobj=io.BytesIO(data)) as tar:
        member = tar.extractfile(MEMBER)
        DEST.write_bytes(member.read())
    print(f"Wrote {DEST} ({DEST.stat().st_size / 1e6:.1f} MB)")
    print('Done. Set DENOISER = "deepfilternet" in prism/config.py to use it.')


if __name__ == "__main__":
    main()
