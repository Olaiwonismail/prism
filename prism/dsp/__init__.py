"""DSP stages for the Prism pipeline.

Each stage exposes `process(block) -> block`, operating on a 1-D float32 array
of mono samples in [-1.0, 1.0], and may carry state across blocks.
"""
