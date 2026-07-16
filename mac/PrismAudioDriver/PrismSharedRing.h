// Shared-memory ring protocol between Prism (writer, prism/ring_output.py)
// and the PrismHAL driver (reader, PrismHAL.c). The Python writer mirrors
// these constants and the exact struct layout (40-byte header, float samples
// at offset 40) -- keep both sides in sync.
//
// Forked from Krasp's KraspSharedRing.h (MIT, (c) 2026 Stepan Pilshchikov).

#ifndef PRISM_SHARED_RING_H
#define PRISM_SHARED_RING_H

#include <stdint.h>

#define PRISM_RING_FILE_PATH "/tmp/com.prism.audio"
#define PRISM_RING_MAGIC 0x5052534Du
#define PRISM_RING_VERSION 1u
#define PRISM_RING_CAPACITY_FRAMES 192000u
#define PRISM_RING_SAMPLE_RATE 48000.0
#define PRISM_RING_CHANNELS 1u

typedef struct PrismSharedRing {
    uint32_t magic;
    uint32_t version;
    uint32_t capacityFrames;
    uint32_t channels;
    double sampleRate;
    uint64_t writeIndex;
    uint64_t generation;
    float samples[PRISM_RING_CAPACITY_FRAMES];
} PrismSharedRing;

#endif
