"""HTTP API around the offline cleaner: upload a recording, get a clean one back.

Reuses the exact same DSP chain as the live app (offline.clean_file ->
prism.pipeline), so the server never touches a sound card or the UI. Run with:

    ./venv/Scripts/python.exe -m uvicorn api:app --host 0.0.0.0 --port 8000

then POST a file to /clean (interactive docs at /docs).
"""

import os
import tempfile

from fastapi import FastAPI, File, HTTPException, Query, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from starlette.background import BackgroundTask

from offline import clean_file
from prism import config

app = FastAPI(title="Prism Cleaner", version="1.0")

# The web UI (docs/, served from GitHub Pages) calls /clean straight from the
# browser, so the response needs CORS headers or the preflight blocks it. No
# credentials are sent, so the allowed origins can stay an explicit short list.
app.add_middleware(
    CORSMiddleware,
    allow_origins=[
        "https://olaiwonismail.github.io",  # GitHub Pages site
        "http://localhost:8080",            # local `http.server -d docs`
    ],
    allow_methods=["GET", "POST"],
    allow_headers=["*"],
)

# Denoisers the caller may request. The ONNX ones are cross-platform; "rnnoise"
# needs the native wheel (see notes in README / CLAUDE.md before deploying).
ALLOWED_DENOISERS = {"rnnoise", "gtcrn", "deepfilternet", "none"}


@app.get("/")
def health():
    """Liveness check + which denoiser runs when the caller doesn't pick one."""
    return {"status": "ok", "default_denoiser": config.DENOISER}


@app.post("/clean")
def clean(
    file: UploadFile = File(...),
    denoiser: str | None = Query(
        default=None,
        description="rnnoise | gtcrn | deepfilternet | none (default: config.DENOISER)",
    ),
):
    """Clean an uploaded recording and stream back a 48 kHz mono WAV.

    The processed audio is always 48 kHz (the pipeline's rate); we don't
    resample back to the source rate.
    """
    if denoiser is not None and denoiser.lower() not in ALLOWED_DENOISERS:
        raise HTTPException(
            status_code=400,
            detail=f"denoiser must be one of {sorted(ALLOWED_DENOISERS)}",
        )

    # Stage the upload and the result as temp files; clean_file is path-based.
    suffix = os.path.splitext(file.filename or "")[1] or ".wav"
    in_fd, in_path = tempfile.mkstemp(suffix=suffix)
    out_fd, out_path = tempfile.mkstemp(suffix=".wav")
    os.close(out_fd)

    try:
        with os.fdopen(in_fd, "wb") as f:
            f.write(file.file.read())
        clean_file(in_path, out_path, denoiser=denoiser)
    except Exception as exc:
        _safe_remove(in_path)
        _safe_remove(out_path)
        raise HTTPException(status_code=400, detail=f"could not process file: {exc}")

    os.remove(in_path)  # done with the upload; the response only needs out_path

    # Delete the result after it has been streamed to the client.
    return FileResponse(
        out_path,
        media_type="audio/wav",
        filename="cleaned.wav",
        background=BackgroundTask(_safe_remove, out_path),
    )


def _safe_remove(path):
    try:
        os.remove(path)
    except OSError:
        pass
