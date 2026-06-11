"""Offline sanity checks for the DSP pipeline (no audio devices needed).

Run:  ./venv/Scripts/python.exe -m tests.test_pipeline
Streams synthetic signals through the pipeline and asserts the high-pass
removes low-frequency rumble, the noise gate silences quiet input, and the
RNNoise stage attenuates broadband noise while honouring the block contract.
"""

import numpy as np

from prism import config
from prism.pipeline import build_default_pipeline, RNNoiseDenoiser

FS, N = config.SAMPLERATE, config.BLOCKSIZE
N_BLOCKS = 32
T = np.arange(N * N_BLOCKS) / FS  # continuous timeline across all blocks


def to_i16(x):
    return (np.clip(x, -1, 1) * 32767).astype(np.int16)[:, None]


def rms(x):
    x = np.asarray(x, dtype=np.float32)
    if x.dtype != np.float32 or x.max() > 1.5:  # int16 input
        x = x / 32768.0
    return float(np.sqrt(np.mean(x * x)))


def run_signal(signal, rnnoise=False):
    """Stream a continuous signal block-by-block; return the second half of
    the output (past filter/gate warm-up transients)."""
    pipeline = build_default_pipeline()
    for stage in pipeline.stages:
        if RNNoiseDenoiser is not None and isinstance(stage, RNNoiseDenoiser):
            stage.enabled = rnnoise
    out = np.concatenate([
        pipeline.process_int16(to_i16(signal[i:i + N]))
        for i in range(0, len(signal), N)
    ])
    return out[len(out) // 2:]


def check_phase1():
    rumble = 0.5 * np.sin(2 * np.pi * 40 * T)       # below high-pass cutoff
    tone = 0.5 * np.sin(2 * np.pi * 300 * T)        # speech band, loud
    quiet = 0.001 * np.sin(2 * np.pi * 300 * T)     # below gate threshold

    r_rumble = rms(run_signal(rumble))
    r_tone = rms(run_signal(tone))
    r_quiet = rms(run_signal(quiet))
    in_rumble, in_tone = rms(rumble[len(T) // 2:]), rms(tone[len(T) // 2:])

    print(f"40Hz rumble : in={in_rumble:.4f}  out={r_rumble:.4f}")
    print(f"300Hz tone  : in={in_tone:.4f}  out={r_tone:.4f}")
    print(f"quiet       : out={r_quiet:.6f}")

    # High-pass should strongly attenuate 40 Hz rumble (~ -13 dB at 2nd order).
    assert r_rumble < in_rumble * 0.3, "high-pass did not attenuate rumble"
    # A loud speech-band tone should pass through largely intact.
    assert r_tone > in_tone * 0.7, "speech-band tone was over-attenuated"
    # Quiet input should be gated to near silence.
    assert r_quiet < 1e-3, "noise gate did not silence quiet input"

    out = build_default_pipeline().process_int16(to_i16(tone[:N]))
    assert out.dtype == np.int16 and out.ndim == 1 and out.shape == (N,)


def check_rnnoise():
    if RNNoiseDenoiser is None:
        print("RNNoise unavailable -- skipping its checks.")
        return

    # Broadband stationary noise should be strongly attenuated.
    rng = np.random.default_rng(0)
    noise = (0.05 * rng.standard_normal(len(T))).astype(np.float32)
    stage = RNNoiseDenoiser()
    out = np.concatenate([stage.process(noise[i:i + N])
                          for i in range(0, len(noise), N)])
    in_rms, out_rms = rms(noise[len(T) // 2:]), rms(out[len(out) // 2:])
    print(f"white noise : in={in_rms:.4f}  out={out_rms:.4f} "
          f"(speech_prob={stage.speech_prob:.2f})")
    assert out_rms < in_rms * 0.5, "RNNoise did not attenuate broadband noise"

    # Block contract: same number of samples out per call, any block size.
    for blocksize in (N, 1024, 160):
        stage = RNNoiseDenoiser()
        total_in = total_out = 0
        for i in range(0, 48000, blocksize):
            block = noise[i:i + blocksize]
            out = stage.process(block)
            assert len(out) == len(block), f"length broken at B={blocksize}"
            assert out.dtype == np.float32
            total_in += len(block)
            total_out += len(out)
        assert total_in == total_out

    # Disabled stage must be a perfect passthrough.
    stage = RNNoiseDenoiser(enabled=False)
    block = noise[:N]
    assert np.array_equal(stage.process(block), block)


def main():
    check_phase1()
    check_rnnoise()
    print("\nAll pipeline checks passed.")


if __name__ == "__main__":
    main()
