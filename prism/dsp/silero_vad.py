"""Silero VAD speech gate (Phase 3): gate on *speech*, not loudness.

Silero VAD is a tiny (~2.3 MB) MIT-licensed neural voice activity detector. It
answers one question per window -- "is this human speech?" -- as a probability
in [0, 1]. This stage uses that probability to drive a gate with the same
attack/release/hold envelope as the RMS NoiseGate, so it keeps quiet speech the
RMS gate would clip and drops loud non-speech (keyboard, fan, door) the RMS gate
would pass.

Two facts shape the design:

  1. Silero runs at **16 kHz on fixed 512-sample windows (~32 ms)**. Putting the
     audio *through* a 32 ms buffer would blow Prism's latency budget, so we
     don't: the VAD **observes a downsampled copy** to update a running speech
     probability while the real audio passes the stage untouched (same
     "watch but don't touch" idea as meters.py). The ~32 ms detection lag is
     absorbed by the gate's hold time -- no latency is added to the signal.
  2. The model is stateful (LSTM), carrying state tensors across windows, and
     its exact input names differ by version (v4: input/sr/h/c; v5:
     input/state/sr). We **introspect the graph at load** instead of hardcoding:
     the int input is the sample rate, ``input`` is the audio, and every other
     input is a recurrent state zero-initialised to its declared shape.

onnxruntime is an optional dependency and the model is fetched separately (see
scripts/fetch_silero_vad.py); if either is missing the constructor raises OSError
so the pipeline falls back to the RMS NoiseGate.
"""

from pathlib import Path

import numpy as np

from .gtcrn import _Decimator  # stateful 48 kHz -> 16 kHz anti-alias resampler

_VAD_SR = 16000
_VAD_WINDOW = 512   # samples at 16 kHz Silero consumes per step (fixed)


def _resolve(path):
    """Resolve a model path relative to the repo root (prism/dsp/.. = root)."""
    p = Path(path)
    return p if p.is_absolute() else Path(__file__).resolve().parents[2] / p


class SileroVAD:
    """Streaming speech-activity gate for float32 audio in [-1.0, 1.0].

    Presents the same gate contract as NoiseGate (threshold/attack/release/hold)
    but opens on detected speech rather than loudness. ``enabled`` flips
    processing live; when disabled the block passes through untouched.
    ``speech_prob`` exposes the latest detection for the UI/meters.
    """

    IS_VAD = True
    name = "Silero VAD"

    def __init__(self, model_path, threshold, samplerate, blocksize,
                 attack_ms=5.0, release_ms=150.0, hold_ms=200.0, enabled=True):
        try:
            import onnxruntime as ort
        except ImportError as exc:
            raise OSError("onnxruntime not installed (pip install onnxruntime)") from exc
        path = _resolve(model_path)
        if not path.exists():
            raise OSError(f"Silero VAD model not found: {path} "
                          "(run scripts/fetch_silero_vad.py)")
        opts = ort.SessionOptions()
        opts.intra_op_num_threads = 1  # one tiny window: bound CPU, avoid oversubscription
        self._sess = ort.InferenceSession(
            str(path), sess_options=opts, providers=["CPUExecutionProvider"])

        # Introspect inputs: the int input is the sample rate, "input" is the
        # audio, everything else is a recurrent state (v4: h/c, v5: state).
        self._audio_name = None
        self._sr_name = None
        self._state_names = []
        self._states = {}
        for inp in self._sess.get_inputs():
            if "int" in inp.type:
                self._sr_name = inp.name
            elif inp.name == "input":
                self._audio_name = inp.name
            else:
                shape = [d if isinstance(d, int) and d > 0 else 1 for d in inp.shape]
                self._state_names.append(inp.name)
                self._states[inp.name] = np.zeros(shape, dtype=np.float32)
        if self._audio_name is None:  # fall back to the first float input
            self._audio_name = next(i.name for i in self._sess.get_inputs()
                                    if "int" not in i.type)
        self._out_names = [o.name for o in self._sess.get_outputs()]

        self.threshold = float(threshold)
        self.enabled = enabled
        self.speech_prob = 0.0

        # 16 kHz analysis path: downsample a copy, buffer into 512-sample windows.
        self._down = _Decimator()
        self._buf = np.zeros(0, dtype=np.float32)

        # Gate envelope -- same maths as NoiseGate, driven by speech_prob.
        self.block_ms = 1000.0 * blocksize / samplerate
        self.attack_step = min(1.0, self.block_ms / max(attack_ms, 1e-6))
        self.release_step = min(1.0, self.block_ms / max(release_ms, 1e-6))
        self.hold_ms = hold_ms
        self._gain = 0.0       # current smoothed gain; start closed (silent)
        self._hold_left = 0.0  # ms the gate stays open after speech drops out

    def _infer(self, window):
        """Run one 512-sample 16 kHz window through Silero, advancing its state.
        Returns the speech probability in [0, 1]."""
        feed = {self._audio_name: window.reshape(1, -1).astype(np.float32)}
        if self._sr_name is not None:
            feed[self._sr_name] = np.array(_VAD_SR, dtype=np.int64)
        for name in self._state_names:
            feed[name] = self._states[name]
        out = self._sess.run(self._out_names, feed)
        for name, val in zip(self._state_names, out[1:]):
            self._states[name] = val
        return float(np.asarray(out[0]).reshape(-1)[0])

    def process(self, block):
        if not self.enabled or block.size == 0:
            return block
        # Observe via a downsampled copy -- the audio path itself is untouched,
        # so the model's 32 ms window adds no latency to the signal.
        self._buf = np.concatenate((self._buf, self._down.process(block)))
        while self._buf.size >= _VAD_WINDOW:
            window, self._buf = self._buf[:_VAD_WINDOW], self._buf[_VAD_WINDOW:]
            self.speech_prob = self._infer(window)

        # Gate the original block on the latest speech probability. The hold
        # keeps the gate open through brief gaps (and across the detection lag).
        if self.speech_prob >= self.threshold:
            self._hold_left = self.hold_ms
        else:
            self._hold_left = max(0.0, self._hold_left - self.block_ms)
        target = 1.0 if self._hold_left > 0.0 else 0.0
        step = self.attack_step if target > self._gain else self.release_step
        start_gain = self._gain
        end_gain = start_gain + (target - start_gain) * step
        ramp = np.linspace(start_gain, end_gain, block.size, dtype=np.float32)
        self._gain = end_gain
        return block * ramp
