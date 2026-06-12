"""Display-only meters that summarise the stream for the UI.

These never touch the audio. The callback feeds one block's stats per call and
the UI polls the two readings:

  - ``noise_floor_db``: how loud the room is when no one is speaking. Learned
    only from frames RNNoise marks as non-speech, so a voice never inflates it,
    and adapted slowly so it reads the steady ambient floor rather than a
    momentary clatter.
  - ``reduction_db``: how much quieter the cleaned output is than the raw input
    right now (input dBFS minus output dBFS), smoothed. The "look how much junk
    we're killing" number.
"""


class NoiseMeter:
    def __init__(self, block_ms, speech_threshold=0.5,
                 floor_tau_ms=1500.0, reduction_tau_ms=250.0, db_floor=-80.0):
        self.speech_threshold = speech_threshold
        self.db_floor = db_floor
        # One-pole smoothing: per-block step toward the target reading.
        self._floor_alpha = min(1.0, block_ms / max(floor_tau_ms, 1e-6))
        self._reduction_alpha = min(1.0, block_ms / max(reduction_tau_ms, 1e-6))
        self.noise_floor_db = db_floor   # ambient room level between words
        self.reduction_db = 0.0          # how much output dropped below input

    def update(self, in_db, out_db, speech_prob):
        """Fold one block's stats into both readings.

        ``speech_prob`` is RNNoise's per-frame voice probability, or None when
        AI denoising is off/unavailable — without a VAD we can't tell speech
        from noise, so the floor holds its last estimate rather than guess.
        """
        if speech_prob is not None and speech_prob < self.speech_threshold:
            self.noise_floor_db += (in_db - self.noise_floor_db) * self._floor_alpha
        # Clamp at 0 so output-above-input jitter never reads as added noise.
        target = max(0.0, in_db - out_db)
        self.reduction_db += (target - self.reduction_db) * self._reduction_alpha

    def reset(self):
        """Forget the learned floor (e.g. after a mic switch)."""
        self.noise_floor_db = self.db_floor
        self.reduction_db = 0.0
