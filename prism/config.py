"""Central configuration for Prism.

All tunable knobs live here so the rest of the code stays declarative.
"""

import sys as _sys

# --- Audio stream -----------------------------------------------------------
# RNNoise is trained on 48 kHz audio and consumes fixed 480-sample (10 ms)
# frames, so the whole stream runs at 48 kHz with one block == one RNNoise
# frame. WASAPI shared mode resamples if the device format differs.
SAMPLERATE = 48000
BLOCKSIZE = 480           # frames per block (10 ms at 48 kHz)
DTYPE = "int16"

# --- Virtual device routing (per platform) ----------------------------------
# Windows: VB-Audio Cable. Prism writes to the "CABLE Input" playback device;
#          apps record from "CABLE Output". If missing, prism/bootstrap.py can
#          run a bundled VB-Cable installer (one UAC prompt + one reboot).
# Linux:   a PipeWire loopback pair Prism spawns at startup (no install):
#          Prism writes to the "Prism Virtual Cable" sink, apps record from
#          the "Prism Microphone" source.
# macOS:   no output device at all. The bundled PrismAudio HAL driver exposes
#          a "Prism Microphone" input device fed by a shared-memory ring file
#          Prism writes directly (see prism/ring_output.py + mac/). CABLE_NAME
#          is unused there.
LINUX_SINK_NODE = "prism_cable"
LINUX_SOURCE_NODE = "prism_mic"
LINUX_SINK_DESCRIPTION = "Prism Virtual Cable"
LINUX_SOURCE_DESCRIPTION = "Prism Microphone"
if _sys.platform.startswith("linux"):
    CABLE_NAME = LINUX_SINK_DESCRIPTION
else:
    CABLE_NAME = "CABLE Input"  # VB-Audio Cable (Windows)

# macOS virtual mic (input device published by the PrismAudio HAL driver).
MAC_VIRTUAL_MIC_NAME = "Prism Microphone"
MAC_DRIVER_BUNDLE = "PrismAudio.driver"       # bundled with the app / CI artifact
MAC_RING_FILE = "/tmp/com.prism.audio"        # must match PrismSharedRing.h

# Bundled VB-Cable installer (Windows), relative to the app/repo root; fetch
# with scripts/fetch_vbcable.py before packaging. Bundled unmodified.
VBCABLE_INSTALLER = "installers/VBCABLE_Setup_x64.exe"

# Never use these as the mic: our own virtual devices (feedback loop) and
# Windows mapper pseudo-devices, which just forward to the default device.
INPUT_EXCLUDE = ("CABLE", "Prism Microphone", "Microsoft Sound Mapper",
                 "Primary Sound")

# --- High-pass filter -------------------------------------------------------
# Removes low-frequency rumble/hum. Speech fundamentals start ~85 Hz, so a
# 90 Hz cutoff trims rumble without thinning voices.
HIGHPASS_CUTOFF_HZ = 90
HIGHPASS_ORDER = 2

# --- Noise gate -------------------------------------------------------------
# Which gate runs at the end of the pipeline:
#   "rms"  - NoiseGate: opens on loudness (RMS) above NOISE_GATE_THRESHOLD_DB
#   "vad"  - SileroVAD: opens on detected *speech*, so it keeps quiet speech an
#            RMS gate would clip and drops loud non-speech a RMS gate would pass
# "vad" needs onnxruntime + the Silero model (scripts/fetch_silero_vad.py); if
# either is missing the pipeline says why and falls back to the RMS gate.
GATE_MODE = "rms"

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

# --- Voice activity detection (Phase 3) -------------------------------------
# The "vad" gate (GATE_MODE above) uses Silero VAD. It observes a 16 kHz
# downsampled copy of the audio and gates on speech probability, reusing the
# noise-gate attack/release/hold envelope above. The model isn't committed;
# fetch it with scripts/fetch_silero_vad.py.
SILERO_MODEL = "models/silero_vad/silero_vad.onnx"
VAD_THRESHOLD = 0.5               # Silero speech probability above this = speech

# --- AI noise removal --------------------------------------------------------
# Which denoiser runs in the pipeline:
#   "rnnoise"       - light: ~10 ms latency, ~1 ms/block CPU, bundled in a wheel
#   "gtcrn"         - ultra-light NN: tiny ~0.5 MB ONNX model, very low CPU; runs
#                     at 16 kHz so it resamples internally (~40 ms latency)
#   "deepfilternet" - stronger: ~32 ms latency, ~6 ms/block CPU, 13 MB ONNX model
#   "none"          - skip AI denoising (high-pass + gate only)
# The ONNX denoisers need onnxruntime + their model file; if either is missing
# the pipeline prints why and falls back to the platform default below.
# RNNoise is the Windows default (its shared library ships in the pyrnnoise
# wheel there); elsewhere GTCRN is the default and the fallback -- pure
# onnxruntime + a portable ONNX model, no native library to source.
DENOISER = "rnnoise" if _sys.platform == "win32" else "gtcrn"
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

