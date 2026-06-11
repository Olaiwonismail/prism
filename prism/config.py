"""Central configuration for Prism.

All tunable knobs live here so the rest of the code stays declarative.
"""

# --- Audio stream -----------------------------------------------------------
SAMPLERATE = 16000
BLOCKSIZE = 1024          # frames per block (~64 ms at 16 kHz)
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
# Silences the mic when the signal is quieter than the threshold (i.e. between
# words). Attack/release are smoothing times to avoid clicks.
NOISE_GATE_THRESHOLD_DB = -25.0   # below this loudness -> treated as silence
                                  # measured: noise floor ~-35 dBFS, voice ~-20 dBFS.
                                  # Lower toward -28 if quiet speech gets clipped.
NOISE_GATE_ATTACK_MS = 5.0        # how fast the gate opens on speech
NOISE_GATE_RELEASE_MS = 120.0     # how fast the gate closes on silence

