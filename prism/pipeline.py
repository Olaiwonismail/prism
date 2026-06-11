"""The Prism processing pipeline: an ordered chain of DSP/AI stages."""

import numpy as np

from . import config
from .dsp.highpass import HighPassFilter
from .dsp.noise_gate import NoiseGate

try:
    from .dsp.rnnoise_denoise import RNNoiseDenoiser
except OSError as exc:  # missing pyrnnoise wheel / shared library
    RNNoiseDenoiser = None
    _RNNOISE_ERROR = exc

_INT16_MAX = 32767
_INT16_SCALE = 32768.0


class Pipeline:
    """Ordered chain of stages applied to mono audio blocks.

    Stages operate on 1-D float32 samples in [-1.0, 1.0]. The pipeline owns the
    int16 <-> float conversion so the audio callback stays trivial. New stages
    (RNNoise, VAD, Demucs, ...) plug in by appending to ``stages``.
    """

    def __init__(self, stages):
        self.stages = stages

    def process_int16(self, indata):
        """Process an (frames, 1) int16 block -> (frames,) int16 mono block."""
        x = indata[:, 0].astype(np.float32) / _INT16_SCALE
        for stage in self.stages:
            x = stage.process(x)
        x = np.clip(x * _INT16_SCALE, -_INT16_SCALE, _INT16_MAX)
        return x.astype(np.int16)


def build_default_pipeline():
    """Current chain: high-pass filter -> RNNoise denoiser -> noise gate.

    The gate runs *after* RNNoise so it acts on the cleaned signal, whose
    noise floor is far lower. That lets it use a low threshold that gates true
    silence without clipping soft speech onsets (fricatives, quiet word
    starts) -- which a gate on the raw, noisy mic would chop.
    """
    stages = [
        HighPassFilter(
            cutoff_hz=config.HIGHPASS_CUTOFF_HZ,
            samplerate=config.SAMPLERATE,
            order=config.HIGHPASS_ORDER,
        ),
    ]
    if config.RNNOISE_ENABLED:
        if RNNoiseDenoiser is None:
            print(f"RNNoise unavailable, running without it: {_RNNOISE_ERROR}")
        else:
            stages.append(RNNoiseDenoiser(mix=config.RNNOISE_MIX))
    stages.append(NoiseGate(
        threshold_db=config.NOISE_GATE_THRESHOLD_DB,
        samplerate=config.SAMPLERATE,
        blocksize=config.BLOCKSIZE,
        attack_ms=config.NOISE_GATE_ATTACK_MS,
        release_ms=config.NOISE_GATE_RELEASE_MS,
        hold_ms=config.NOISE_GATE_HOLD_MS,
    ))
    return Pipeline(stages)
