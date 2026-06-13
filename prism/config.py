"""Central configuration for Prism.

All tunable knobs live here so the rest of the code stays declarative.
"""

# --- Audio stream -----------------------------------------------------------
# RNNoise is trained on 48 kHz audio and consumes fixed 480-sample (10 ms)
# frames, so the whole stream runs at 48 kHz with one block == one RNNoise
# frame. WASAPI shared mode resamples if the device format differs.
SAMPLERATE = 48000
BLOCKSIZE = 480           # frames per block (10 ms at 48 kHz)
DTYPE = "int16"

# Virtual cable output device, matched by name substring (VB-Audio Cable).
CABLE_NAME = "CABLE Input"
# Never use these as the mic: VB-Cable devices (feedback loop) and Windows
# mapper pseudo-devices, which just forward to the default device.
INPUT_EXCLUDE = ("CABLE", "Microsoft Sound Mapper", "Primary Sound")

# --- High-pass filter -------------------------------------------------------
# Removes low-frequency rumble/hum. Speech fundamentals start ~85 Hz, so a
# 90 Hz cutoff trims rumble without thinning voices.
HIGHPASS_CUTOFF_HZ = 90
HIGHPASS_ORDER = 2

# --- Noise gate -------------------------------------------------------------
# Silences the mic between words. Runs AFTER RNNoise (see pipeline.py), so it
# sees the cleaned signal whose noise floor is far below the raw mic's. That
# lets the threshold sit low enough to pass soft speech onsets while still
# gating true silence -- a gate on the raw mic had to sit at -25 (above the
# ~-35 dBFS noise floor) and clipped quiet consonants.
NOISE_GATE_THRESHOLD_DB = -45.0   # below this loudness -> treated as silence
                                  # raise toward -35 if residual noise leaks
                                  # through; lower if soft speech still clips.
NOISE_GATE_ATTACK_MS = 5.0        # how fast the gate opens on speech
NOISE_GATE_RELEASE_MS = 150.0     # how fast the gate fades out after the hold
NOISE_GATE_HOLD_MS = 200.0        # stays fully open this long after speech
                                  # drops out, so word ends/gaps aren't chopped

# --- AI noise removal --------------------------------------------------------
# Which denoiser runs in the pipeline:
#   "rnnoise"       - light: ~10 ms latency, ~1 ms/block CPU, bundled in a wheel
#   "gtcrn"         - ultra-light NN: tiny ~0.5 MB ONNX model, very low CPU; runs
#                     at 16 kHz so it resamples internally (~40 ms latency)
#   "deepfilternet" - stronger: ~32 ms latency, ~6 ms/block CPU, 13 MB ONNX model
#   "none"          - skip AI denoising (high-pass + gate only)
# The ONNX denoisers need onnxruntime + their model file; if either is missing
# the pipeline prints why and falls back to RNNoise.
DENOISER = "rnnoise"
DENOISE_ENABLED = True            # master on/off for the AI denoiser (UI toggle)
DENOISE_MIX = 1.0                 # dry/wet: 0.0 = bypass, 1.0 = fully denoised
                                  # exposed live as the UI "strength" slider

# DeepFilterNet3 ONNX model, relative to the repo root (see
# scripts/fetch_deepfilternet.py to download it).
DEEPFILTERNET_MODEL = "models/deepfilternet3/denoiser_model.onnx"

# GTCRN streaming ONNX model, relative to the repo root (see
# scripts/fetch_gtcrn.py to download it).
GTCRN_MODEL = "models/gtcrn/gtcrn_simple.onnx"

# --- Noise meter (display only) ---------------------------------------------
# Two readings the UI polls; see prism/meters.py. None of this touches audio.
NOISE_METER_SPEECH_THRESHOLD = 0.5    # RNNoise VAD prob below this = "no speech"
NOISE_METER_FLOOR_TAU_MS = 1500.0     # how slowly the room-noise reading adapts
NOISE_METER_REDUCTION_TAU_MS = 250.0  # smoothing on the "noise removed" reading

