"""GTCRN denoiser stage (Phase 2): the ultra-light AI option.

GTCRN ("Grouped Temporal Convolutional Recurrent Network") is a tiny ~48 K
parameter model (33 MMACs/s) that cleans speech for a fraction of
DeepFilterNet's CPU while still being a neural net -- the light end of Prism's
AI denoisers. It runs through ONNX Runtime (CPU), one short-time spectrum frame
per step, carrying three recurrent/cache tensors across calls.

Unlike RNNoise (48 kHz, STFT baked into the C library) and DeepFilterNet
(48 kHz, STFT inside the ONNX graph), GTCRN works at **16 kHz with the STFT
outside the model**: n_fft=512, hop=256, sqrt-Hann window. So this stage owns
three jobs the other two don't:

  1. resample Prism's 48 kHz blocks down to 16 kHz and back -- a clean 3:1
     ratio, done with stateful anti-alias FIRs so it streams without edge clicks;
  2. run a streaming STFT / overlap-add around the model. Analysis and synthesis
     both use the sqrt-Hann window, whose square (a Hann window) is COLA at 50 %
     overlap, so plain overlap-add reconstructs with no extra normalisation;
  3. carry the model's conv/tra/inter caches frame to frame.

The model itself is frame-synchronous (it adds no latency of its own), but the
resampler FIRs and the 256-hop STFT/overlap-add do. Both are measured once at
import: ``_GROUP_48`` is the FIRs' positional delay, ``_DEFICIT_48`` is how far
the STFT must fill before it emits. The stage pre-fills its wet output FIFO past
the deficit (so it never underflows -- keeping the block contract: same samples
out as in, any block size) and delays a parallel dry line by deficit + group
delay, so the dry/wet strength blend stays phase-aligned.

onnxruntime is an optional dependency and the model is fetched separately (see
scripts/fetch_gtcrn.py); if either is missing the constructor raises OSError so
the pipeline falls back to RNNoise / Phase 1.
"""

from pathlib import Path

import numpy as np
from scipy.signal import firwin, lfilter, windows

from .. import config

# --- 48 kHz <-> 16 kHz resampling -------------------------------------------
_RATIO = config.SAMPLERATE // 16000   # 3: Prism runs at 48 kHz, GTCRN at 16 kHz
_FILTER_TAPS = 159                     # anti-alias FIR length (linear phase)
_CUTOFF_HZ = 7300                      # below 8 kHz (16 kHz Nyquist), with margin

# --- GTCRN STFT (all at 16 kHz) ---------------------------------------------
_NFFT = 512
_HOP = 256
_NFREQ = _NFFT // 2 + 1                # 257 one-sided bins == the model's freq dim
# Periodic (DFT-even) sqrt-Hann == torch.hann_window(512).pow(0.5), what the
# model was trained with. Its square is a Hann window: COLA at 50 % overlap.
_WINDOW = np.sqrt(windows.hann(_NFFT, sym=False)).astype(np.float32)


def _resolve(path):
    """Resolve a model path relative to the repo root (prism/dsp/.. = root)."""
    p = Path(path)
    return p if p.is_absolute() else Path(__file__).resolve().parents[2] / p


def _lowpass(gain):
    """Anti-alias / anti-image lowpass FIR, scaled by ``gain``."""
    h = firwin(_FILTER_TAPS, _CUTOFF_HZ, fs=config.SAMPLERATE).astype(np.float32)
    return h * np.float32(gain)


class _Decimator:
    """Stateful 48 kHz -> 16 kHz: anti-alias FIR, then keep every 3rd sample.

    The kept-sample phase is tracked across blocks so a block size that isn't a
    multiple of 3 still streams continuously (Prism's 480-sample default is, so
    in practice this emits exactly block/3 samples per call)."""

    def __init__(self):
        self._h = _lowpass(1.0)
        self._zi = np.zeros(len(self._h) - 1, dtype=np.float32)
        self._phase = 0  # index (mod 3) of the running input stream

    def process(self, x):
        if x.size == 0:
            return x
        y, self._zi = lfilter(self._h, 1.0, x, zi=self._zi)
        start = (-self._phase) % _RATIO
        self._phase = (self._phase + x.size) % _RATIO
        return y[start::_RATIO].astype(np.float32)


class _Interpolator:
    """Stateful 16 kHz -> 48 kHz: zero-stuff x3, then anti-image lowpass.

    The lowpass is scaled by the ratio to compensate for the energy lost to the
    inserted zeros. Output is exactly 3x the input length, every call."""

    def __init__(self):
        self._h = _lowpass(_RATIO)
        self._zi = np.zeros(len(self._h) - 1, dtype=np.float32)

    def process(self, x):
        if x.size == 0:
            return x
        up = np.zeros(x.size * _RATIO, dtype=np.float32)
        up[::_RATIO] = x
        y, self._zi = lfilter(self._h, 1.0, up, zi=self._zi)
        return y.astype(np.float32)


class _Stft:
    """Streaming STFT -> per-frame spectral op -> overlap-add ISTFT (16 kHz).

    Accumulates 16 kHz samples; whenever a full 512-sample window is buffered it
    windows it, takes the rfft, hands the spectrum to ``enhance``, inverts, and
    overlap-adds the synthesis-windowed result, advancing one 256-sample hop.
    Each completed hop is returned. ``enhance`` maps a length-257 complex
    spectrum to an enhanced one (identity for latency measurement)."""

    def __init__(self):
        self._in = np.zeros(0, dtype=np.float32)        # samples awaiting a frame
        self._ola = np.zeros(_NFFT, dtype=np.float32)   # overlap-add accumulator
        self._out = np.zeros(0, dtype=np.float32)       # finished hops to return

    def process(self, x16, enhance):
        self._in = np.concatenate((self._in, x16))
        while self._in.size >= _NFFT:
            spec = np.fft.rfft(self._in[:_NFFT] * _WINDOW)
            td = np.fft.irfft(enhance(spec), _NFFT).astype(np.float32) * _WINDOW
            self._ola[:_NFFT] += td
            self._out = np.concatenate((self._out, self._ola[:_HOP].copy()))
            self._ola = np.concatenate((self._ola[_HOP:],
                                        np.zeros(_HOP, dtype=np.float32)))
            self._in = self._in[_HOP:]
        out, self._out = self._out, np.zeros(0, dtype=np.float32)
        return out


def _measure_group_delay():
    """Positional delay (48 kHz samples) the resampler FIRs add: where an impulse
    re-emerges in the output stream. The STFT/overlap-add is time-aligned (COLA),
    so it contributes none -- only the two linear-phase FIRs shift the peak.

    The model is frame-synchronous (no latency of its own), so this runs with the
    spectral op as identity. Deterministic, so it runs once at import."""
    down, stft, up = _Decimator(), _Stft(), _Interpolator()
    n = config.BLOCKSIZE
    sig = np.zeros(n * 64, dtype=np.float32)
    sig[n] = 1.0  # one block in, clear of the FIR warm-up edge
    out = np.concatenate([
        up.process(stft.process(down.process(sig[i:i + n]), lambda s: s))
        for i in range(0, sig.size, n)
    ])
    return int(np.argmax(np.abs(out))) - n  # subtract the impulse's own offset


def _measure_deficit():
    """Most the wet output lags the input during warm-up (48 kHz samples): how
    far the STFT must fill before it first emits. This is a *buffering* delay,
    invisible to the positional measurement above, and it depends only on block
    sizes, so the signal is irrelevant. Sets the wet FIFO pre-fill."""
    down, stft, up = _Decimator(), _Stft(), _Interpolator()
    n = config.BLOCKSIZE
    produced = consumed = deficit = 0
    for _ in range(128):
        wet = up.process(stft.process(down.process(np.zeros(n, dtype=np.float32)),
                                      lambda s: s))
        produced += wet.size
        consumed += n
        deficit = max(deficit, consumed - produced)
    return deficit


_GROUP_48 = _measure_group_delay()
_DEFICIT_48 = _measure_deficit()


class GTCRNDenoiser:
    """Streaming GTCRN stage for float32 audio in [-1.0, 1.0].

    Mirrors the RNNoise / DeepFilterNet contract so all three are drop-in
    swappable: ``enabled`` flips processing live (atomic bool), ``mix`` blends
    dry/denoised per block (1.0 = fully denoised). The dry path is delayed to
    match the resampler + STFT latency so a partial ``mix`` stays phase-aligned.
    """

    IS_DENOISER = True
    name = "GTCRN"

    def __init__(self, model_path, mix=1.0, enabled=True):
        try:
            import onnxruntime as ort
        except ImportError as exc:
            raise OSError("onnxruntime not installed (pip install onnxruntime)") from exc
        path = _resolve(model_path)
        if not path.exists():
            raise OSError(f"GTCRN model not found: {path} "
                          "(run scripts/fetch_gtcrn.py)")
        opts = ort.SessionOptions()
        opts.intra_op_num_threads = 1  # one tiny frame: bound CPU, avoid oversubscription
        self._sess = ort.InferenceSession(
            str(path), sess_options=opts, providers=["CPUExecutionProvider"])
        # Inputs are [spectrum, *caches]; outputs are [enhanced, *updated caches]
        # in the same order, so cache outputs feed straight back by position.
        inputs = self._sess.get_inputs()
        self._spec_name = inputs[0].name
        self._cache_names = [i.name for i in inputs[1:]]
        self._caches = {i.name: np.zeros([int(d) for d in i.shape], dtype=np.float32)
                        for i in inputs[1:]}
        self._out_names = [o.name for o in self._sess.get_outputs()]

        self.mix = float(mix)
        self.enabled = enabled

        self._down = _Decimator()
        self._stft = _Stft()
        self._up = _Interpolator()

        # Pre-fill the wet FIFO past the STFT fill deficit (+ one block of jitter
        # margin) so it never underflows; delay the dry line by that *plus* the
        # resampler group delay so dry and wet stay phase-aligned for the blend.
        wet_pad = _DEFICIT_48 + config.BLOCKSIZE
        self._ready = np.zeros(wet_pad, dtype=np.float32)             # enhanced 48 kHz, awaiting output
        self._dry = np.zeros(wet_pad + _GROUP_48, dtype=np.float32)   # raw 48 kHz, delayed to match wet

    def _enhance(self, spec):
        """Run one 257-bin complex spectrum through GTCRN, advancing its caches."""
        feed = {self._spec_name:
                np.stack((spec.real, spec.imag), axis=-1)
                  .astype(np.float32).reshape(1, _NFREQ, 1, 2)}
        feed.update(self._caches)
        out = self._sess.run(self._out_names, feed)
        for name, val in zip(self._cache_names, out[1:]):
            self._caches[name] = val
        enh = out[0]  # (1, 257, 1, 2)
        return enh[0, :, 0, 0] + 1j * enh[0, :, 0, 1]

    def process(self, block):
        if not self.enabled or block.size == 0:
            return block
        self._dry = np.concatenate((self._dry, block))
        wet = self._up.process(self._stft.process(self._down.process(block),
                                                   self._enhance))
        self._ready = np.concatenate((self._ready, wet))

        dry_out, self._dry = self._dry[:block.size], self._dry[block.size:]
        if self._ready.size < block.size:  # safety net; the pre-fill should prevent this
            self._ready = np.concatenate(
                (np.zeros(block.size - self._ready.size, dtype=np.float32), self._ready))
        wet_out, self._ready = self._ready[:block.size], self._ready[block.size:]
        if self.mix >= 1.0:
            return wet_out
        return self.mix * wet_out + (1.0 - self.mix) * dry_out
