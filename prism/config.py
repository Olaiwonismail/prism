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

# --- RNNoise (AI noise removal) ----------------------------------------------
RNNOISE_ENABLED = True
RNNOISE_MIX = 1.0                 # dry/wet: 0.0 = bypass, 1.0 = fully denoised

