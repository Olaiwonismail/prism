"""Download the VB-Cable installer Prism bundles for self-install (Windows).

VB-Audio distributes VB-Cable as a freeware zip. This fetches it and extracts
the x64 setup into installers/, where prism/bootstrap.py (and Prism.spec at
package time) expect it. The installer is bundled UNMODIFIED -- Prism only
launches it for the user (one UAC prompt); the driver itself remains
VB-Audio's (donationware, https://vb-audio.com/Cable/). Run it once:

    ./venv/Scripts/python.exe scripts/fetch_vbcable.py

Uses only the standard library.
"""

import io
import urllib.request
import zipfile
from pathlib import Path

URL = "https://download.vb-audio.com/Download_CABLE/VBCABLE_Driver_Pack45.zip"
SETUP_NAME = "VBCABLE_Setup_x64.exe"
DEST = Path(__file__).resolve().parents[1] / "installers" / SETUP_NAME


def main():
    if DEST.exists():
        print(f"Already present: {DEST}")
        return
    DEST.parent.mkdir(parents=True, exist_ok=True)
    print(f"Downloading {URL} ...")
    request = urllib.request.Request(URL, headers={"User-Agent": "Mozilla/5.0"})
    with urllib.request.urlopen(request) as resp:
        data = resp.read()
    with zipfile.ZipFile(io.BytesIO(data)) as pack:
        names = [n for n in pack.namelist() if n.endswith(SETUP_NAME)]
        if not names:
            raise SystemExit(
                f"{SETUP_NAME} not found in the pack -- VB-Audio may have "
                f"renamed it; download manually from https://vb-audio.com/Cable/ "
                f"and place the x64 setup at {DEST}"
            )
        DEST.write_bytes(pack.read(names[0]))
    print(f"Wrote {DEST} ({DEST.stat().st_size / 1e6:.1f} MB)")
    print("Done. Prism.spec will bundle it; the app offers it on first run.")


if __name__ == "__main__":
    main()
