"""The Prism processing pipeline: an ordered chain of DSP/AI stages."""

import numpy as np

from . import config
from .dsp.highpass import HighPassFilter
from .dsp.noise_gate import NoiseGate
from .dsp.silero_vad import SileroVAD
from .dsp.deepfilternet import DeepFilterNetDenoiser
from .dsp.gtcrn import GTCRNDenoiser

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


def _build_rnnoise():
    if RNNoiseDenoiser is None:
        print(f"RNNoise unavailable, running without AI denoise: {_RNNOISE_ERROR}")
        return None
    return RNNoiseDenoiser(mix=config.DENOISE_MIX)


def build_denoiser(choice=None):
    """Construct an AI denoiser stage, or None if unavailable.

    ``choice`` defaults to ``config.DENOISER``; pass it explicitly to switch the
    denoiser live. DeepFilterNet needs onnxruntime + the model file; if it can't
    load we say why and fall back to RNNoise, which is bundled and (almost)
    always present.
    """
    choice = (choice or config.DENOISER).lower()
    if choice == "none":
        return None
    if choice == "deepfilternet":
        try:
            return DeepFilterNetDenoiser(config.DEEPFILTERNET_MODEL,
                                         mix=config.DENOISE_MIX)
        except OSError as exc:
            print(f"DeepFilterNet unavailable, falling back to RNNoise: {exc}")
        return _build_rnnoise()
    if choice == "gtcrn":
        try:
            return GTCRNDenoiser(config.GTCRN_MODEL, mix=config.DENOISE_MIX)
        except OSError as exc:
            print(f"GTCRN unavailable, falling back to RNNoise: {exc}")
        return _build_rnnoise()
    return _build_rnnoise()


def _build_rms_gate():
    return NoiseGate(
        threshold_db=config.NOISE_GATE_THRESHOLD_DB,
        samplerate=config.SAMPLERATE,
        blocksize=config.BLOCKSIZE,
        attack_ms=config.NOISE_GATE_ATTACK_MS,
        release_ms=config.NOISE_GATE_RELEASE_MS,
        hold_ms=config.NOISE_GATE_HOLD_MS,
    )


def build_gate(mode=None):
    """Construct the end-of-chain gate per ``config.GATE_MODE``.

    "vad" builds a Silero speech gate (needs onnxruntime + the model); if it
    can't load we say why and fall back to the RMS gate, which is always
    available. Any other value builds the RMS gate directly.
    """
    mode = (mode or config.GATE_MODE).lower()
    if mode == "vad":
        try:
            return SileroVAD(
                config.SILERO_MODEL,
                threshold=config.VAD_THRESHOLD,
                samplerate=config.SAMPLERATE,
                blocksize=config.BLOCKSIZE,
                attack_ms=config.NOISE_GATE_ATTACK_MS,
                release_ms=config.NOISE_GATE_RELEASE_MS,
                hold_ms=config.NOISE_GATE_HOLD_MS,
            )
        except OSError as exc:
            print(f"Silero VAD unavailable, falling back to RMS gate: {exc}")
    return _build_rms_gate()


def build_default_pipeline(denoiser_choice=None):
    """Current chain: high-pass filter -> AI denoiser -> noise gate.

    The gate runs *after* the denoiser so it acts on the cleaned signal, whose
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
    if config.DENOISE_ENABLED:
        denoiser = build_denoiser(denoiser_choice)
        if denoiser is not None:
            stages.append(denoiser)
    stages.append(build_gate())
    return Pipeline(stages)
