#ifndef KRASP_SHARED_RING_H
#define KRASP_SHARED_RING_H

#include <stdint.h>

#define KRASP_RING_FILE_PATH "/tmp/io.github.pilshchikov.krasp.audio"
#define KRASP_RING_MAGIC 0x4B525350u
#define KRASP_RING_VERSION 1u
#define KRASP_RING_CAPACITY_FRAMES 192000u
#define KRASP_RING_SAMPLE_RATE 48000.0
#define KRASP_RING_CHANNELS 1u

typedef struct KraspSharedRing {
    uint32_t magic;
    uint32_t version;
    uint32_t capacityFrames;
    uint32_t channels;
    double sampleRate;
    uint64_t writeIndex;
    uint64_t generation;
    float samples[KRASP_RING_CAPACITY_FRAMES];
} KraspSharedRing;

#endif
