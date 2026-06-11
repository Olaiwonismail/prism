"""Streaming Butterworth high-pass filter."""

import numpy as np
from scipy.signal import butter, sosfilt, sosfilt_zi


class HighPassFilter:
    """Stateful Butterworth high-pass filter for streaming float32 audio.

    Attenuates low-frequency rumble/hum below ``cutoff_hz``. Filter state is
    carried across blocks so there are no discontinuities at block boundaries.
    """

    def __init__(self, cutoff_hz, samplerate, order=2):
        self.sos = butter(
            order, cutoff_hz, btype="highpass", fs=samplerate, output="sos"
        )
        # Per-section steady-state initial conditions, shape (n_sections, 2).
        self._zi = sosfilt_zi(self.sos)
        self._primed = False

    def process(self, block):
        if block.size == 0:
            return block
        if not self._primed:
            # Seed state with the first sample to avoid a startup transient.
            self._zi = self._zi * block[0]
            self._primed = True
        out, self._zi = sosfilt(self.sos, block, zi=self._zi)
        return out.astype(np.float32, copy=False)
