"""DeepFilterNet3 denoiser stage (Phase 2): the stronger, heavier option.

Runs the torch-free streaming DeepFilterNet3 export through ONNX Runtime. The
whole DSP chain -- STFT analysis, ERB/spectral features, the GRU network, deep
filtering, ISTFT synthesis -- lives inside the ONNX graph, so this stage just
feeds one raw audio frame per call and gets one enhanced frame back, carrying
the model's recurrent state tensors across calls.

The export works in fixed 512-sample frames, so an internal FIFO rechunks
Prism's block size into 512-sample frames and back (one frame of buffering when
the block size isn't 512-aligned, as with the 480-sample default). On top of
that the model has ~32 ms of its own algorithmic latency (measured: the impulse
response peaks 3 frames late) -- the cost of its stronger cleanup versus RNNoise.

onnxruntime is an optional dependency and the model file is fetched separately
(see scripts/fetch_deepfilternet.py); if either is missing the constructor
raises OSError so the pipeline falls back to RNNoise / Phase 1.
"""

from pathlib import Path

import numpy as np

FRAME_SIZE = 512               # the export's hop: samples in per call, out per call
_LATENCY_FRAMES = 3            # measured model group delay; dry-path delay for mixing

_INPUT = "input_frame"
# Two normalisation states seed with a linspace, not zeros (from the export's
# own init); endpoints and target shapes come straight from the model graph.
_ERB_NORM_STATE = "erb_norm_state"
_DF_NORM_STATE = "band_unit_norm_state"
_ERB_NORM_LINSPACE = (-60.0, -90.0)
_DF_NORM_LINSPACE = (0.001, 0.0001)


def _resolve(path):
    """Resolve a model path relative to the repo root (prism/dsp/.. = root)."""
    p = Path(path)
    return p if p.is_absolute() else Path(__file__).resolve().parents[2] / p


class DeepFilterNetDenoiser:
    """Streaming DeepFilterNet3 stage for float32 audio in [-1.0, 1.0].

    Mirrors RNNoiseDenoiser's contract so the two are drop-in swappable:
    ``enabled`` flips processing live (atomic bool), ``mix`` blends dry/denoised
    per frame (1.0 = fully denoised). The dry path is delayed to match the
    model's latency so a partial ``mix`` stays phase-aligned (no comb filtering).
    """

    IS_DENOISER = True
    name = "DeepFilterNet3"

    def __init__(self, model_path, mix=1.0, enabled=True):
        try:
            import onnxruntime as ort
        except ImportError as exc:
            raise OSError("onnxruntime not installed (pip install onnxruntime)") from exc
        path = _resolve(model_path)
        if not path.exists():
            raise OSError(f"DeepFilterNet model not found: {path} "
                          "(run scripts/fetch_deepfilternet.py)")
        opts = ort.SessionOptions()
        opts.intra_op_num_threads = 1  # one tiny frame: bound CPU, avoid oversubscription
        self._sess = ort.InferenceSession(
            str(path), sess_options=opts, providers=["CPUExecutionProvider"])
        self._out_names = [o.name for o in self._sess.get_outputs()]
        self.mix = float(mix)
        self.enabled = enabled
        self._states = self._initial_states()
        self._pending = np.zeros(0, dtype=np.float32)  # input awaiting a full frame
        self._ready = np.zeros(0, dtype=np.float32)    # enhanced output FIFO
        self._primed = False                           # one-time latency pad
        # Dry signal delayed by the model's latency so dry/wet mixing lines up.
        self._dry = np.zeros(_LATENCY_FRAMES * FRAME_SIZE, dtype=np.float32)

    def _initial_states(self):
        states = {}
        for inp in self._sess.get_inputs():
            if inp.name != _INPUT:
                states[inp.name] = np.zeros(inp.shape, dtype=np.float32)
        erb = states[_ERB_NORM_STATE]
        states[_ERB_NORM_STATE] = np.linspace(
            *_ERB_NORM_LINSPACE, erb.shape[-1]).astype(np.float32).reshape(erb.shape)
        df = states[_DF_NORM_STATE]
        states[_DF_NORM_STATE] = np.linspace(
            *_DF_NORM_LINSPACE, max(df.shape)).astype(np.float32).reshape(df.shape)
        return states

    def _denoise_frame(self, frame):
        feeds = {_INPUT: np.ascontiguousarray(frame, dtype=np.float32)}
        feeds.update(self._states)
        out = self._sess.run(self._out_names, feeds)
        enhanced = out[0]
        for name, val in zip(self._out_names[1:], out[1:]):
            self._states[name[4:] if name.startswith("new_") else name] = val
        # Advance the dry delay line regardless of mix, so toggling mix mid-
        # stream never blends against a stale dry frame.
        dry = self._dry[:FRAME_SIZE]
        self._dry = np.concatenate((self._dry[FRAME_SIZE:], frame))
        if self.mix >= 1.0:
            return enhanced
        return self.mix * enhanced + (1.0 - self.mix) * dry

    def process(self, block):
        if not self.enabled or block.size == 0:
            return block
        self._pending = np.concatenate((self._pending, block))
        while self._pending.size >= FRAME_SIZE:
            frame, self._pending = (self._pending[:FRAME_SIZE],
                                    self._pending[FRAME_SIZE:])
            self._ready = np.concatenate((self._ready, self._denoise_frame(frame)))
        if self._ready.size < block.size and not self._primed:
            # Block size isn't frame-aligned: pad once so every later call has
            # enough output (adds one frame of latency).
            self._ready = np.concatenate(
                (np.zeros(FRAME_SIZE, dtype=np.float32), self._ready))
            self._primed = True
        out, self._ready = self._ready[:block.size], self._ready[block.size:]
        return out
