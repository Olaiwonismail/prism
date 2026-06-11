"""The Prism processing pipeline: an ordered chain of DSP/AI stages."""

import numpy as np

from . import config
from .dsp.highpass import HighPassFilter
from .dsp.noise_gate import NoiseGate

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
    """Phase 1 chain: high-pass filter -> noise gate."""
    return Pipeline([
        HighPassFilter(
            cutoff_hz=config.HIGHPASS_CUTOFF_HZ,
            samplerate=config.SAMPLERATE,
            order=config.HIGHPASS_ORDER,
        ),
        NoiseGate(
            threshold_db=config.NOISE_GATE_THRESHOLD_DB,
            samplerate=config.SAMPLERATE,
            blocksize=config.BLOCKSIZE,
            attack_ms=config.NOISE_GATE_ATTACK_MS,
            release_ms=config.NOISE_GATE_RELEASE_MS,
        ),
    ])
