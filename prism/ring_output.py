"""Shared-memory ring writer feeding the macOS PrismAudio HAL driver.

On macOS Prism has no virtual *output* device to write to. Instead the
PrismAudio HAL driver (mac/PrismAudioDriver/) publishes a "Prism Microphone"
input device and reads its samples from a memory-mapped ring file. This module
is the writer side of that protocol: the engine pushes each processed block
here instead of into an output stream.

The layout mirrors mac/PrismAudioDriver/PrismSharedRing.h exactly -- a 40-byte
header (magic, version, capacityFrames, channels, sampleRate, writeIndex,
generation) followed by capacityFrames float32 samples. Keep both sides in
sync. Single writer (Prism), single reader (coreaudiod); the reader trails
``writeIndex`` and zero-fills when it runs dry, so a stalled or stopped writer
just produces silence, never garbage.
"""

import mmap
import os
import struct

import numpy as np

# Header: magic, version, capacityFrames, channels (uint32) | sampleRate
# (double) | writeIndex, generation (uint64). Little-endian, natural
# alignment -- identical to the C struct on x86-64/arm64.
_HEADER = struct.Struct("<IIIIdQQ")
_WRITE_INDEX_OFFSET = 24  # byte offset of writeIndex within the header

RING_MAGIC = 0x5052534D  # "PRSM"; must match PrismSharedRing.h
RING_VERSION = 1
RING_CAPACITY_FRAMES = 192000  # 4 s at 48 kHz
RING_CHANNELS = 1


class SharedRingWriter:
    """Writes float32 mono blocks (in [-1.0, 1.0]) into the driver's ring."""

    def __init__(self, path, samplerate):
        size = _HEADER.size + RING_CAPACITY_FRAMES * 4
        # O_CREAT without O_TRUNC: if the file already exists, keep the same
        # inode so a driver that already mmap'd it keeps seeing our writes.
        fd = os.open(path, os.O_RDWR | os.O_CREAT, 0o644)
        try:
            os.ftruncate(fd, size)
            self._mm = mmap.mmap(fd, size)
        finally:
            os.close(fd)

        # Restarting the writer: bump generation, restart writeIndex at 0.
        # The driver re-syncs on writeIndex < its readIndex, so this is safe.
        old = _HEADER.unpack_from(self._mm, 0)
        generation = old[6] + 1 if old[0] == RING_MAGIC else 1
        self._write_index = 0
        _HEADER.pack_into(self._mm, 0, RING_MAGIC, RING_VERSION,
                          RING_CAPACITY_FRAMES, RING_CHANNELS,
                          float(samplerate), 0, generation)
        self._samples = np.frombuffer(self._mm, dtype=np.float32,
                                      count=RING_CAPACITY_FRAMES,
                                      offset=_HEADER.size)

    def write(self, block):
        """Append one float32 block, then publish the new write index.

        Samples land before the index moves, so the reader never sees an index
        that points at unwritten data. (No hard memory barrier from Python; a
        rare stale sample on a racing read beats a wrong-length read.)
        """
        n = len(block)
        pos = self._write_index % RING_CAPACITY_FRAMES
        first = min(n, RING_CAPACITY_FRAMES - pos)
        self._samples[pos:pos + first] = block[:first]
        if first < n:  # wrap around
            self._samples[:n - first] = block[first:]
        self._write_index += n
        struct.pack_into("<Q", self._mm, _WRITE_INDEX_OFFSET,
                         self._write_index)

    def close(self):
        """Release the mapping. The file stays on disk on purpose: coreaudiod
        may still hold its own mapping, and reusing the inode on the next run
        keeps that mapping live."""
        if self._mm is None:
            return
        self._samples = None  # drop the exported buffer so mmap can close
        self._mm.close()
        self._mm = None
