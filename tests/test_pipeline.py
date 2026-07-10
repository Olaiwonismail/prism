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


def check_gate_hold():
    """The hold time should keep the gate open through a brief speech gap so
    word ends aren't chopped, then close once the gap exceeds the hold."""
    from prism.dsp.noise_gate import NoiseGate

    loud = (0.3 * np.sin(2 * np.pi * 300 * T[:N])).astype(np.float32)
    silence = np.zeros(N, dtype=np.float32)
    gate = NoiseGate(threshold_db=-45.0, samplerate=FS, blocksize=N,
                     attack_ms=5.0, release_ms=150.0, hold_ms=200.0)

    for _ in range(8):           # open the gate on speech
        gate.process(loud)
    block_ms = 1000.0 * N / FS
    # Check the gain envelope, not output RMS: silent input is zero at any gain.
    for _ in range(int(50.0 / block_ms)):    # 50 ms gap < 200 ms hold
        gate.process(silence)
    print(f"gate hold    : gain after 50ms gap={gate._gain:.3f} (stays open)")
    assert gate._gain > 0.5, "gate closed during a gap shorter than the hold time"

    # Release is a fractional approach (asymptotic), so allow ample time.
    for _ in range(int(2000.0 / block_ms)):   # long silence >> hold + release
        gate.process(silence)
    assert gate._gain < 1e-3, "gate did not close after the hold expired"


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


def check_noise_meter():
    """The room-floor reading should learn from non-speech frames, ignore
    speech frames, and hold when there's no VAD; reduction tracks in-out gap."""
    from prism.meters import NoiseMeter

    block_ms = 1000.0 * N / FS

    # Floor converges toward the input level seen on non-speech frames.
    meter = NoiseMeter(block_ms, speech_threshold=0.5, floor_tau_ms=200.0)
    for _ in range(200):
        meter.update(in_db=-50.0, out_db=-80.0, speech_prob=0.1)  # noise only
    print(f"noise meter  : floor={meter.noise_floor_db:.1f} dB "
          f"reduction={meter.reduction_db:.1f} dB")
    assert abs(meter.noise_floor_db - -50.0) < 1.0, "floor did not track noise"
    assert abs(meter.reduction_db - 30.0) < 1.0, "reduction wrong (in-out gap)"

    # A loud voice (high speech_prob) must not inflate the floor.
    before = meter.noise_floor_db
    for _ in range(200):
        meter.update(in_db=-10.0, out_db=-12.0, speech_prob=0.95)
    assert abs(meter.noise_floor_db - before) < 0.5, "speech leaked into floor"

    # With no VAD (speech_prob=None) the floor holds rather than guessing.
    held = meter.noise_floor_db
    for _ in range(200):
        meter.update(in_db=-10.0, out_db=-12.0, speech_prob=None)
    assert meter.noise_floor_db == held, "floor moved without a VAD"


def check_deepfilternet():
    """DeepFilterNet attenuates broadband noise and honours the block contract.
    Skips cleanly if onnxruntime or the model file isn't present."""
    from prism.dsp.deepfilternet import DeepFilterNetDenoiser

    try:
        stage = DeepFilterNetDenoiser(config.DEEPFILTERNET_MODEL)
    except OSError as exc:
        print(f"DeepFilterNet unavailable -- skipping its checks ({exc}).")
        return

    rng = np.random.default_rng(0)
    noise = (0.05 * rng.standard_normal(len(T))).astype(np.float32)
    out = np.concatenate([stage.process(noise[i:i + N])
                          for i in range(0, len(noise), N)])
    in_rms, out_rms = rms(noise[len(T) // 2:]), rms(out[len(out) // 2:])
    print(f"DFN noise   : in={in_rms:.4f}  out={out_rms:.4f}")
    assert out_rms < in_rms * 0.5, "DeepFilterNet did not attenuate broadband noise"

    # Block contract: same number of samples out per call, any block size --
    # the 480->512 FIFO must not drop or duplicate samples.
    for blocksize in (N, 1024, 160):
        stage = DeepFilterNetDenoiser(config.DEEPFILTERNET_MODEL)
        total_in = total_out = 0
        for i in range(0, 48000, blocksize):
            block = noise[i:i + blocksize]
            o = stage.process(block)
            assert len(o) == len(block), f"length broken at B={blocksize}"
            assert o.dtype == np.float32
            total_in += len(block)
            total_out += len(o)
        assert total_in == total_out

    # Disabled stage must be a perfect passthrough.
    stage = DeepFilterNetDenoiser(config.DEEPFILTERNET_MODEL, enabled=False)
    block = noise[:N]
    assert np.array_equal(stage.process(block), block)


def check_gtcrn():
    """GTCRN attenuates broadband noise and honours the block contract despite
    its internal 48<->16 kHz resampling. Skips if onnxruntime or the model file
    isn't present."""
    from prism.dsp.gtcrn import GTCRNDenoiser

    try:
        stage = GTCRNDenoiser(config.GTCRN_MODEL)
    except OSError as exc:
        print(f"GTCRN unavailable -- skipping its checks ({exc}).")
        return

    rng = np.random.default_rng(0)
    noise = (0.05 * rng.standard_normal(len(T))).astype(np.float32)
    out = np.concatenate([stage.process(noise[i:i + N])
                          for i in range(0, len(noise), N)])
    in_rms, out_rms = rms(noise[len(T) // 2:]), rms(out[len(out) // 2:])
    print(f"GTCRN noise : in={in_rms:.4f}  out={out_rms:.4f}")
    assert out_rms < in_rms * 0.5, "GTCRN did not attenuate broadband noise"

    # Block contract: same number of samples out per call, any block size --
    # the resamplers + 16 kHz STFT FIFO must not drop or duplicate samples.
    for blocksize in (N, 1024, 160):
        stage = GTCRNDenoiser(config.GTCRN_MODEL)
        total_in = total_out = 0
        for i in range(0, 48000, blocksize):
            block = noise[i:i + blocksize]
            o = stage.process(block)
            assert len(o) == len(block), f"length broken at B={blocksize}"
            assert o.dtype == np.float32
            total_in += len(block)
            total_out += len(o)
        assert total_in == total_out

    # Disabled stage must be a perfect passthrough.
    stage = GTCRNDenoiser(config.GTCRN_MODEL, enabled=False)
    block = noise[:N]
    assert np.array_equal(stage.process(block), block)


def check_silero_vad():
    """Silero VAD honours the block contract, passes through when disabled, and
    reports low speech probability on silence (so its gate closes). Detection of
    real speech is verified live in the app, not from synthetic tones. Skips if
    onnxruntime or the model file isn't present."""
    from prism.dsp.silero_vad import SileroVAD

    def make_stage(**kw):
        return SileroVAD(config.SILERO_MODEL, threshold=config.VAD_THRESHOLD,
                         samplerate=FS, blocksize=N, **kw)

    try:
        stage = make_stage()
    except OSError as exc:
        print(f"Silero VAD unavailable -- skipping its checks ({exc}).")
        return

    # Block contract: same number of samples out per call, any block size --
    # the 48->16 kHz decimator + 512-sample window FIFO must not drop samples.
    rng = np.random.default_rng(0)
    noise = (0.05 * rng.standard_normal(len(T))).astype(np.float32)
    for blocksize in (N, 1024, 160):
        s = make_stage()
        total_in = total_out = 0
        for i in range(0, 48000, blocksize):
            block = noise[i:i + blocksize]
            o = s.process(block)
            assert len(o) == len(block), f"length broken at B={blocksize}"
            assert o.dtype == np.float32
            total_in += len(block)
            total_out += len(o)
        assert total_in == total_out

    # Silence: speech probability stays low and the gate closes to near silence.
    silence = np.zeros(len(T), dtype=np.float32)
    out = np.concatenate([stage.process(silence[i:i + N])
                          for i in range(0, len(silence), N)])
    print(f"Silero VAD  : silence speech_prob={stage.speech_prob:.2f} "
          f"out_rms={rms(out[len(out) // 2:]):.6f}")
    assert stage.speech_prob < 0.5, "VAD saw speech in pure silence"
    assert rms(out[len(out) // 2:]) < 1e-3, "VAD gate did not close on silence"

    # Disabled stage must be a perfect passthrough.
    s = make_stage(enabled=False)
    block = noise[:N]
    assert np.array_equal(s.process(block), block)


def main():
    check_phase1()
    check_gate_hold()
    check_rnnoise()
    check_noise_meter()
    check_deepfilternet()
    check_gtcrn()
    check_silero_vad()
    print("\nAll pipeline checks passed.")


if __name__ == "__main__":
    main()
