"""RNNoise denoiser stage: recurrent-NN broadband noise removal (Phase 2).

Binds directly to the rnnoise shared library bundled in the ``pyrnnoise``
wheel via ctypes. The package's own Python wrapper is not used (it drags in
audio-file I/O deps that are broken and unneeded for streaming).

RNNoise operates on fixed 480-sample (10 ms) frames at 48 kHz, with samples
as floats in int16 range. The stage rechunks arbitrary block sizes through an
internal FIFO; when the block size is a multiple of the frame size (Prism's
default config) the FIFO stays empty and no extra latency is added.
"""

import ctypes
import importlib.util
import os
import platform
import sys

import numpy as np

_INT16_SCALE = 32768.0

_LIB_NAMES = {
    "Windows": "rnnoise.dll",
    "Linux": "librnnoise.so",
    "Darwin": "librnnoise.dylib",
}


def _library_dir():
    """Directory holding the rnnoise shared library.

    Frozen (PyInstaller) builds bundle the library at the app root; source
    installs load it from inside the pyrnnoise wheel.
    """
    if getattr(sys, "frozen", False):
        return getattr(sys, "_MEIPASS", os.path.dirname(sys.executable))
    spec = importlib.util.find_spec("pyrnnoise")
    if spec is None or spec.origin is None:
        raise OSError("pyrnnoise is not installed (pip install pyrnnoise)")
    return os.path.dirname(spec.origin)


def _load_librnnoise():
    """Load the rnnoise shared library (model weights baked in)."""
    lib_name = _LIB_NAMES.get(platform.system())
    if lib_name is None:
        raise OSError(f"rnnoise: unsupported platform {platform.system()}")
    lib = ctypes.CDLL(os.path.join(_library_dir(), lib_name))
    lib.rnnoise_create.argtypes = [ctypes.c_void_p]
    lib.rnnoise_create.restype = ctypes.c_void_p
    lib.rnnoise_destroy.argtypes = [ctypes.c_void_p]
    lib.rnnoise_destroy.restype = None
    lib.rnnoise_get_frame_size.restype = ctypes.c_int
    lib.rnnoise_process_frame.argtypes = [
        ctypes.c_void_p,
        ctypes.POINTER(ctypes.c_float),
        ctypes.POINTER(ctypes.c_float),
    ]
    lib.rnnoise_process_frame.restype = ctypes.c_float
    return lib


_lib = _load_librnnoise()
FRAME_SIZE = _lib.rnnoise_get_frame_size()  # 480 samples = 10 ms at 48 kHz


class RNNoiseDenoiser:
    """Streaming RNNoise stage for float32 audio in [-1.0, 1.0].

    ``enabled`` may be flipped at any time from another thread (plain bool,
    atomic under the GIL) for live A/B toggling. ``mix`` blends dry and
    denoised audio per frame (1.0 = fully denoised). ``speech_prob`` is the
    voice probability of the last processed frame, free from the model —
    UI noise meter material.
    """

    IS_DENOISER = True  # marks this as the pipeline's denoiser stage (see audio.py)
    name = "RNNoise"

    def __init__(self, mix=1.0, enabled=True):
        self.mix = float(mix)
        self.enabled = enabled
        self.speech_prob = 0.0
        self._state = _lib.rnnoise_create(None)
        self._pending = np.zeros(0, dtype=np.float32)  # input awaiting a full frame
        self._ready = np.zeros(0, dtype=np.float32)    # denoised output FIFO
        self._primed = False  # one-time latency padding for unaligned block sizes

    def __del__(self):
        if getattr(self, "_state", None):
            _lib.rnnoise_destroy(self._state)
            self._state = None

    def _denoise_frame(self, frame):
        """Run one FRAME_SIZE float32 [-1, 1] frame through the model."""
        x = np.ascontiguousarray(frame * _INT16_SCALE, dtype=np.float32)
        ptr = x.ctypes.data_as(ctypes.POINTER(ctypes.c_float))
        self.speech_prob = float(_lib.rnnoise_process_frame(self._state, ptr, ptr))
        wet = x / _INT16_SCALE
        if self.mix >= 1.0:
            return wet
        return self.mix * wet + (1.0 - self.mix) * frame

    def process(self, block):
        if not self.enabled or block.size == 0:
            return block
        self._pending = np.concatenate((self._pending, block))
        while self._pending.size >= FRAME_SIZE:
            frame, self._pending = (self._pending[:FRAME_SIZE],
                                    self._pending[FRAME_SIZE:])
            self._ready = np.concatenate((self._ready, self._denoise_frame(frame)))
        if self._ready.size < block.size and not self._primed:
            # Block size isn't frame-aligned: pad once with one frame of
            # silence so every later call has enough output (10 ms latency).
            self._ready = np.concatenate(
                (np.zeros(FRAME_SIZE, dtype=np.float32), self._ready))
            self._primed = True
        out, self._ready = self._ready[:block.size], self._ready[block.size:]
        return out
