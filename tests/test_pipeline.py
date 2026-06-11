"""Offline sanity checks for the DSP pipeline (no audio devices needed).

Run:  ./venv/Scripts/python.exe -m tests.test_pipeline
Feeds synthetic signals through the default pipeline and asserts the high-pass
removes low-frequency rumble and the noise gate silences quiet input.
"""

import numpy as np

from prism import config
from prism.pipeline import build_default_pipeline

FS, N = config.SAMPLERATE, config.BLOCKSIZE
T = np.arange(N) / FS


def to_i16(x):
    return (np.clip(x, -1, 1) * 32767).astype(np.int16)[:, None]


def rms(block_i16):
    return float(np.sqrt(np.mean((block_i16.astype(np.float32) / 32768.0) ** 2)))


def run_blocks(signal, n_blocks=8):
    """Push the same block through a fresh pipeline n times; return last output."""
    pipeline = build_default_pipeline()
    out = None
    for _ in range(n_blocks):
        out = pipeline.process_int16(to_i16(signal))
    return out


def main():
    rumble = 0.5 * np.sin(2 * np.pi * 40 * T)     # below high-pass cutoff
    tone = 0.5 * np.sin(2 * np.pi * 300 * T)        # speech band, loud
    quiet = 0.001 * np.sin(2 * np.pi * 300 * T)     # below gate threshold

    out_rumble = run_blocks(rumble)
    out_tone = run_blocks(tone)
    out_quiet = run_blocks(quiet)

    in_rumble, in_tone = rms(to_i16(rumble)), rms(to_i16(tone))
    r_rumble, r_tone, r_quiet = rms(out_rumble), rms(out_tone), rms(out_quiet)

    print(f"40Hz rumble : in={in_rumble:.4f}  out={r_rumble:.4f}")
    print(f"300Hz tone  : in={in_tone:.4f}  out={r_tone:.4f}")
    print(f"quiet       : in={rms(to_i16(quiet)):.4f}  out={r_quiet:.4f}")
    print(f"output shape={out_tone.shape} dtype={out_tone.dtype}")

    # High-pass should strongly attenuate 40 Hz rumble. A 2nd-order filter at a
    # 90 Hz cutoff gives ~12 dB/octave, so ~40 Hz lands around -13 dB (~0.23x).
    assert r_rumble < in_rumble * 0.3, "high-pass did not attenuate rumble"
    # A loud speech-band tone should pass through largely intact.
    assert r_tone > in_tone * 0.7, "speech-band tone was over-attenuated"
    # Quiet input should be gated to near silence.
    assert r_quiet < 1e-3, "noise gate did not silence quiet input"
    # Output contract: 1-D int16.
    assert out_tone.dtype == np.int16 and out_tone.ndim == 1

    print("\nAll pipeline checks passed.")


if __name__ == "__main__":
    main()
