"""Streaming RMS noise gate."""

import numpy as np


class NoiseGate:
    """Simple RMS noise gate for streaming float32 audio.

    When a block's loudness drops below ``threshold_db`` the gate closes
    (attenuates toward silence); when speech returns it opens again. Gain is
    ramped across each block (attack/release smoothing) so there are no clicks.
    """

    def __init__(self, threshold_db, samplerate, blocksize,
                 attack_ms=5.0, release_ms=120.0, hold_ms=150.0):
        # Threshold as a linear amplitude (RMS), not power.
        self.threshold = 10.0 ** (threshold_db / 20.0)
        self.block_ms = 1000.0 * blocksize / samplerate
        # Fraction of the remaining gap toward the target gain to close per block.
        self.attack_step = min(1.0, self.block_ms / max(attack_ms, 1e-6))
        self.release_step = min(1.0, self.block_ms / max(release_ms, 1e-6))
        self.hold_ms = hold_ms
        self._gain = 0.0       # current smoothed gain; start closed (silent)
        self._hold_left = 0.0  # ms the gate stays open after speech drops out

    def process(self, block):
        if block.size == 0:
            return block
        rms = float(np.sqrt(np.mean(block * block)))
        if rms >= self.threshold:
            self._hold_left = self.hold_ms  # speech present: (re)arm the hold
        else:
            self._hold_left = max(0.0, self._hold_left - self.block_ms)
        # Stay open through brief dips/gaps so word ends aren't chopped.
        target = 1.0 if self._hold_left > 0.0 else 0.0
        step = self.attack_step if target > self._gain else self.release_step
        start_gain = self._gain
        end_gain = start_gain + (target - start_gain) * step
        # Linear gain ramp across the block avoids audible clicks.
        ramp = np.linspace(start_gain, end_gain, block.size, dtype=np.float32)
        self._gain = end_gain
        return block * ramp
