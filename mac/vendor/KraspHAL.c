#include <CoreAudio/AudioHardware.h>
#include <CoreAudio/AudioServerPlugIn.h>
#include <CoreFoundation/CoreFoundation.h>
#include <CoreFoundation/CFPlugInCOM.h>
#include <mach/mach_time.h>
#include <pthread.h>
#include <stdbool.h>
#include <fcntl.h>
#include <stddef.h>
#include <stdint.h>
#include <string.h>
#include <sys/mman.h>
#include <unistd.h>

#include "../../Shared/KraspSharedRing.h"

enum {
    kKraspObjectDevice = 2,
    kKraspObjectInputStream = 3,
    kKraspBufferFrameSize = 512
};

static HRESULT STDMETHODCALLTYPE QueryInterface(void* inDriver, REFIID inUUID, LPVOID* outInterface);
static ULONG STDMETHODCALLTYPE AddRef(void* inDriver);
static ULONG STDMETHODCALLTYPE Release(void* inDriver);
static OSStatus STDMETHODCALLTYPE Initialize(AudioServerPlugInDriverRef inDriver, AudioServerPlugInHostRef inHost);
static OSStatus STDMETHODCALLTYPE CreateDevice(AudioServerPlugInDriverRef inDriver, CFDictionaryRef inDescription, const AudioServerPlugInClientInfo* inClientInfo, AudioObjectID* outDeviceObjectID);
static OSStatus STDMETHODCALLTYPE DestroyDevice(AudioServerPlugInDriverRef inDriver, AudioObjectID inDeviceObjectID);
static OSStatus STDMETHODCALLTYPE AddDeviceClient(AudioServerPlugInDriverRef inDriver, AudioObjectID inDeviceObjectID, const AudioServerPlugInClientInfo* inClientInfo);
static OSStatus STDMETHODCALLTYPE RemoveDeviceClient(AudioServerPlugInDriverRef inDriver, AudioObjectID inDeviceObjectID, const AudioServerPlugInClientInfo* inClientInfo);
static OSStatus STDMETHODCALLTYPE PerformDeviceConfigurationChange(AudioServerPlugInDriverRef inDriver, AudioObjectID inDeviceObjectID, UInt64 inChangeAction, void* inChangeInfo);
static OSStatus STDMETHODCALLTYPE AbortDeviceConfigurationChange(AudioServerPlugInDriverRef inDriver, AudioObjectID inDeviceObjectID, UInt64 inChangeAction, void* inChangeInfo);
static Boolean STDMETHODCALLTYPE HasProperty(AudioServerPlugInDriverRef inDriver, AudioObjectID inObjectID, pid_t inClientProcessID, const AudioObjectPropertyAddress* inAddress);
static OSStatus STDMETHODCALLTYPE IsPropertySettable(AudioServerPlugInDriverRef inDriver, AudioObjectID inObjectID, pid_t inClientProcessID, const AudioObjectPropertyAddress* inAddress, Boolean* outIsSettable);
static OSStatus STDMETHODCALLTYPE GetPropertyDataSize(AudioServerPlugInDriverRef inDriver, AudioObjectID inObjectID, pid_t inClientProcessID, const AudioObjectPropertyAddress* inAddress, UInt32 inQualifierDataSize, const void* inQualifierData, UInt32* outDataSize);
static OSStatus STDMETHODCALLTYPE GetPropertyData(AudioServerPlugInDriverRef inDriver, AudioObjectID inObjectID, pid_t inClientProcessID, const AudioObjectPropertyAddress* inAddress, UInt32 inQualifierDataSize, const void* inQualifierData, UInt32 inDataSize, UInt32* outDataSize, void* outData);
static OSStatus STDMETHODCALLTYPE SetPropertyData(AudioServerPlugInDriverRef inDriver, AudioObjectID inObjectID, pid_t inClientProcessID, const AudioObjectPropertyAddress* inAddress, UInt32 inQualifierDataSize, const void* inQualifierData, UInt32 inDataSize, const void* inData);
static OSStatus STDMETHODCALLTYPE StartIO(AudioServerPlugInDriverRef inDriver, AudioObjectID inDeviceObjectID, UInt32 inClientID);
static OSStatus STDMETHODCALLTYPE StopIO(AudioServerPlugInDriverRef inDriver, AudioObjectID inDeviceObjectID, UInt32 inClientID);
static OSStatus STDMETHODCALLTYPE GetZeroTimeStamp(AudioServerPlugInDriverRef inDriver, AudioObjectID inDeviceObjectID, UInt32 inClientID, Float64* outSampleTime, UInt64* outHostTime, UInt64* outSeed);
static OSStatus STDMETHODCALLTYPE WillDoIOOperation(AudioServerPlugInDriverRef inDriver, AudioObjectID inDeviceObjectID, UInt32 inClientID, UInt32 inOperationID, Boolean* outWillDo, Boolean* outWillDoInPlace);
static OSStatus STDMETHODCALLTYPE BeginIOOperation(AudioServerPlugInDriverRef inDriver, AudioObjectID inDeviceObjectID, UInt32 inClientID, UInt32 inOperationID, UInt32 inIOBufferFrameSize, const AudioServerPlugInIOCycleInfo* inIOCycleInfo);
static OSStatus STDMETHODCALLTYPE DoIOOperation(AudioServerPlugInDriverRef inDriver, AudioObjectID inDeviceObjectID, AudioObjectID inStreamObjectID, UInt32 inClientID, UInt32 inOperationID, UInt32 inIOBufferFrameSize, const AudioServerPlugInIOCycleInfo* inIOCycleInfo, void* ioMainBuffer, void* ioSecondaryBuffer);
static OSStatus STDMETHODCALLTYPE EndIOOperation(AudioServerPlugInDriverRef inDriver, AudioObjectID inDeviceObjectID, UInt32 inClientID, UInt32 inOperationID, UInt32 inIOBufferFrameSize, const AudioServerPlugInIOCycleInfo* inIOCycleInfo);

static AudioServerPlugInDriverInterface gDriverInterface = {
    NULL,
    QueryInterface,
    AddRef,
    Release,
    Initialize,
    CreateDevice,
    DestroyDevice,
    AddDeviceClient,
    RemoveDeviceClient,
    PerformDeviceConfigurationChange,
    AbortDeviceConfigurationChange,
    HasProperty,
    IsPropertySettable,
    GetPropertyDataSize,
    GetPropertyData,
    SetPropertyData,
    StartIO,
    StopIO,
    GetZeroTimeStamp,
    WillDoIOOperation,
    BeginIOOperation,
    DoIOOperation,
    EndIOOperation
};

static AudioServerPlugInDriverInterface* gDriverInterfacePtr = &gDriverInterface;
static AudioServerPlugInHostRef gHost = NULL;
static UInt32 gRefCount = 1;
static pthread_mutex_t gStateMutex = PTHREAD_MUTEX_INITIALIZER;
static UInt32 gRunningClients = 0;
static Float64 gSampleTime = 0;
static UInt64 gZeroHostTime = 0;
static UInt64 gTimestampSeed = 1;
static uint64_t gReadIndex = 0;
static KraspSharedRing* gRing = NULL;
static size_t gRingByteCount = sizeof(KraspSharedRing);

static AudioStreamBasicDescription StreamFormat(void) {
    AudioStreamBasicDescription format;
    memset(&format, 0, sizeof(format));
    format.mSampleRate = KRASP_RING_SAMPLE_RATE;
    format.mFormatID = kAudioFormatLinearPCM;
    format.mFormatFlags = kAudioFormatFlagIsFloat | kAudioFormatFlagIsPacked | kAudioFormatFlagsNativeEndian;
    format.mBytesPerPacket = sizeof(float);
    format.mFramesPerPacket = 1;
    format.mBytesPerFrame = sizeof(float);
    format.mChannelsPerFrame = KRASP_RING_CHANNELS;
    format.mBitsPerChannel = 32;
    return format;
}

static AudioStreamRangedDescription StreamRangedFormat(void) {
    AudioStreamRangedDescription ranged;
    memset(&ranged, 0, sizeof(ranged));
    ranged.mFormat = StreamFormat();
    ranged.mSampleRateRange.mMinimum = KRASP_RING_SAMPLE_RATE;
    ranged.mSampleRateRange.mMaximum = KRASP_RING_SAMPLE_RATE;
    return ranged;
}

static bool ScopeMatches(AudioObjectPropertyScope actual, AudioObjectPropertyScope expected) {
    return actual == expected || actual == kAudioObjectPropertyScopeGlobal || actual == kAudioObjectPropertyScopeWildcard;
}

static bool ObjectExists(AudioObjectID objectID) {
    return objectID == kAudioObjectPlugInObject || objectID == kKraspObjectDevice || objectID == kKraspObjectInputStream;
}

static AudioClassID ClassForObject(AudioObjectID objectID) {
    switch (objectID) {
        case kAudioObjectPlugInObject: return kAudioPlugInClassID;
        case kKraspObjectDevice: return kAudioDeviceClassID;
        case kKraspObjectInputStream: return kAudioStreamClassID;
        default: return kAudioObjectClassID;
    }
}

static AudioClassID BaseClassForObject(AudioObjectID objectID) {
    (void)objectID;
    return kAudioObjectClassID;
}

static AudioObjectID OwnerForObject(AudioObjectID objectID) {
    switch (objectID) {
        case kAudioObjectPlugInObject: return kAudioObjectUnknown;
        case kKraspObjectDevice: return kAudioObjectPlugInObject;
        case kKraspObjectInputStream: return kKraspObjectDevice;
        default: return kAudioObjectUnknown;
    }
}

static void CopyCFString(CFStringRef string, UInt32 inDataSize, UInt32* outDataSize, void* outData) {
    if (inDataSize >= sizeof(CFStringRef)) {
        CFRetain(string);
        *((CFStringRef*)outData) = string;
        *outDataSize = sizeof(CFStringRef);
    }
}

static void CopyUInt32(UInt32 value, UInt32 inDataSize, UInt32* outDataSize, void* outData) {
    if (inDataSize >= sizeof(UInt32)) {
        *((UInt32*)outData) = value;
        *outDataSize = sizeof(UInt32);
    }
}

static void CopyObjectID(AudioObjectID value, UInt32 inDataSize, UInt32* outDataSize, void* outData) {
    if (inDataSize >= sizeof(AudioObjectID)) {
        *((AudioObjectID*)outData) = value;
        *outDataSize = sizeof(AudioObjectID);
    }
}

static void CopyFloat64(Float64 value, UInt32 inDataSize, UInt32* outDataSize, void* outData) {
    if (inDataSize >= sizeof(Float64)) {
        *((Float64*)outData) = value;
        *outDataSize = sizeof(Float64);
    }
}

static UInt32 StreamConfigurationSize(UInt32 channels) {
    return channels == 0 ? (UInt32)offsetof(AudioBufferList, mBuffers) : (UInt32)(offsetof(AudioBufferList, mBuffers) + sizeof(AudioBuffer));
}

static void CopyStreamConfiguration(UInt32 channels, UInt32 inDataSize, UInt32* outDataSize, void* outData) {
    UInt32 size = StreamConfigurationSize(channels);
    if (inDataSize >= size) {
        AudioBufferList* list = (AudioBufferList*)outData;
        if (channels == 0) {
            list->mNumberBuffers = 0;
        } else {
            list->mNumberBuffers = 1;
            list->mBuffers[0].mNumberChannels = channels;
            list->mBuffers[0].mDataByteSize = 0;
            list->mBuffers[0].mData = NULL;
        }
        *outDataSize = size;
    }
}

static void OpenRingIfNeeded(void) {
    if (gRing != NULL) {
        return;
    }

    int fd = open(KRASP_RING_FILE_PATH, O_RDONLY, 0);
    if (fd < 0) {
        return;
    }

    void* mapped = mmap(NULL, gRingByteCount, PROT_READ, MAP_SHARED, fd, 0);
    close(fd);
    if (mapped == MAP_FAILED) {
        return;
    }

    KraspSharedRing* ring = (KraspSharedRing*)mapped;
    if (ring->magic != KRASP_RING_MAGIC || ring->version != KRASP_RING_VERSION || ring->capacityFrames != KRASP_RING_CAPACITY_FRAMES) {
        munmap(mapped, gRingByteCount);
        return;
    }

    gRing = ring;
    gReadIndex = ring->writeIndex;
}

static void ReadRing(float* destination, UInt32 frameCount) {
    memset(destination, 0, frameCount * sizeof(float));
    OpenRingIfNeeded();

    KraspSharedRing* ring = gRing;
    if (ring == NULL || ring->magic != KRASP_RING_MAGIC) {
        return;
    }

    uint64_t writeIndex = ring->writeIndex;
    if (writeIndex < gReadIndex) {
        gReadIndex = writeIndex;
        return;
    }

    uint64_t available = writeIndex - gReadIndex;
    if (available > KRASP_RING_CAPACITY_FRAMES) {
        gReadIndex = writeIndex - frameCount;
        available = frameCount;
    }

    UInt32 framesToCopy = frameCount;
    if (available < frameCount) {
        UInt32 silenceFrames = frameCount - (UInt32)available;
        destination += silenceFrames;
        framesToCopy = (UInt32)available;
    }

    for (UInt32 frame = 0; frame < framesToCopy; frame++) {
        destination[frame] = ring->samples[(gReadIndex + frame) % KRASP_RING_CAPACITY_FRAMES];
    }
    gReadIndex += framesToCopy;
}

__attribute__((visibility("default")))
void* KraspPlugInFactory(CFAllocatorRef allocator, CFUUIDRef typeUUID) {
    (void)allocator;
    if (CFEqual(typeUUID, kAudioServerPlugInTypeUUID)) {
        AddRef(&gDriverInterfacePtr);
        return &gDriverInterfacePtr;
    }
    return NULL;
}

static HRESULT STDMETHODCALLTYPE QueryInterface(void* inDriver, REFIID inUUID, LPVOID* outInterface) {
    (void)inDriver;
    if (outInterface == NULL) {
        return E_POINTER;
    }

    CFUUIDRef uuid = CFUUIDCreateFromUUIDBytes(NULL, inUUID);
    if (CFEqual(uuid, IUnknownUUID) || CFEqual(uuid, kAudioServerPlugInDriverInterfaceUUID)) {
        AddRef(&gDriverInterfacePtr);
        *outInterface = &gDriverInterfacePtr;
        CFRelease(uuid);
        return S_OK;
    }

    *outInterface = NULL;
    CFRelease(uuid);
    return E_NOINTERFACE;
}

static ULONG STDMETHODCALLTYPE AddRef(void* inDriver) {
    (void)inDriver;
    return __sync_add_and_fetch(&gRefCount, 1);
}

static ULONG STDMETHODCALLTYPE Release(void* inDriver) {
    (void)inDriver;
    return __sync_sub_and_fetch(&gRefCount, 1);
}

static OSStatus STDMETHODCALLTYPE Initialize(AudioServerPlugInDriverRef inDriver, AudioServerPlugInHostRef inHost) {
    (void)inDriver;
    gHost = inHost;
    gZeroHostTime = mach_absolute_time();
    OpenRingIfNeeded();
    return noErr;
}

static OSStatus STDMETHODCALLTYPE CreateDevice(AudioServerPlugInDriverRef inDriver, CFDictionaryRef inDescription, const AudioServerPlugInClientInfo* inClientInfo, AudioObjectID* outDeviceObjectID) {
    (void)inDriver;
    (void)inDescription;
    (void)inClientInfo;
    if (outDeviceObjectID != NULL) {
        *outDeviceObjectID = kAudioObjectUnknown;
    }
    return kAudioHardwareUnsupportedOperationError;
}

static OSStatus STDMETHODCALLTYPE DestroyDevice(AudioServerPlugInDriverRef inDriver, AudioObjectID inDeviceObjectID) {
    (void)inDriver;
    (void)inDeviceObjectID;
    return kAudioHardwareUnsupportedOperationError;
}

static OSStatus STDMETHODCALLTYPE AddDeviceClient(AudioServerPlugInDriverRef inDriver, AudioObjectID inDeviceObjectID, const AudioServerPlugInClientInfo* inClientInfo) {
    (void)inDriver;
    (void)inDeviceObjectID;
    (void)inClientInfo;
    return noErr;
}

static OSStatus STDMETHODCALLTYPE RemoveDeviceClient(AudioServerPlugInDriverRef inDriver, AudioObjectID inDeviceObjectID, const AudioServerPlugInClientInfo* inClientInfo) {
    (void)inDriver;
    (void)inDeviceObjectID;
    (void)inClientInfo;
    return noErr;
}

static OSStatus STDMETHODCALLTYPE PerformDeviceConfigurationChange(AudioServerPlugInDriverRef inDriver, AudioObjectID inDeviceObjectID, UInt64 inChangeAction, void* inChangeInfo) {
    (void)inDriver;
    (void)inDeviceObjectID;
    (void)inChangeAction;
    (void)inChangeInfo;
    return noErr;
}

static OSStatus STDMETHODCALLTYPE AbortDeviceConfigurationChange(AudioServerPlugInDriverRef inDriver, AudioObjectID inDeviceObjectID, UInt64 inChangeAction, void* inChangeInfo) {
    (void)inDriver;
    (void)inDeviceObjectID;
    (void)inChangeAction;
    (void)inChangeInfo;
    return noErr;
}

static Boolean STDMETHODCALLTYPE HasProperty(AudioServerPlugInDriverRef inDriver, AudioObjectID inObjectID, pid_t inClientProcessID, const AudioObjectPropertyAddress* inAddress) {
    (void)inDriver;
    (void)inClientProcessID;
    if (inAddress == NULL || !ObjectExists(inObjectID)) {
        return false;
    }

    switch (inAddress->mSelector) {
        case kAudioObjectPropertyBaseClass:
        case kAudioObjectPropertyClass:
        case kAudioObjectPropertyOwner:
        case kAudioObjectPropertyName:
        case kAudioObjectPropertyManufacturer:
        case kAudioObjectPropertyOwnedObjects:
            return true;
    }

    if (inObjectID == kAudioObjectPlugInObject) {
        switch (inAddress->mSelector) {
            case kAudioPlugInPropertyBundleID:
            case kAudioPlugInPropertyDeviceList:
            case kAudioPlugInPropertyTranslateUIDToDevice:
            case kAudioPlugInPropertyResourceBundle:
                return true;
            default:
                return false;
        }
    }

    if (inObjectID == kKraspObjectDevice) {
        switch (inAddress->mSelector) {
            case kAudioObjectPropertyModelName:
            case kAudioDevicePropertyDeviceUID:
            case kAudioDevicePropertyModelUID:
            case kAudioDevicePropertyTransportType:
            case kAudioDevicePropertyRelatedDevices:
            case kAudioDevicePropertyClockDomain:
            case kAudioDevicePropertyDeviceIsAlive:
            case kAudioDevicePropertyDeviceIsRunning:
            case kAudioDevicePropertyDeviceCanBeDefaultDevice:
            case kAudioDevicePropertyDeviceCanBeDefaultSystemDevice:
            case kAudioDevicePropertyLatency:
            case kAudioDevicePropertyStreams:
            case kAudioObjectPropertyControlList:
            case kAudioDevicePropertySafetyOffset:
            case kAudioDevicePropertyNominalSampleRate:
            case kAudioDevicePropertyAvailableNominalSampleRates:
            case kAudioDevicePropertyIsHidden:
            case kAudioDevicePropertyBufferFrameSize:
            case kAudioDevicePropertyBufferFrameSizeRange:
            case kAudioDevicePropertyStreamConfiguration:
            case kAudioDevicePropertyZeroTimeStampPeriod:
                return true;
            default:
                return false;
        }
    }

    if (inObjectID == kKraspObjectInputStream) {
        switch (inAddress->mSelector) {
            case kAudioStreamPropertyIsActive:
            case kAudioStreamPropertyDirection:
            case kAudioStreamPropertyTerminalType:
            case kAudioStreamPropertyStartingChannel:
            case kAudioStreamPropertyLatency:
            case kAudioStreamPropertyVirtualFormat:
            case kAudioStreamPropertyAvailableVirtualFormats:
            case kAudioStreamPropertyPhysicalFormat:
            case kAudioStreamPropertyAvailablePhysicalFormats:
                return true;
            default:
                return false;
        }
    }

    return false;
}

static OSStatus STDMETHODCALLTYPE IsPropertySettable(AudioServerPlugInDriverRef inDriver, AudioObjectID inObjectID, pid_t inClientProcessID, const AudioObjectPropertyAddress* inAddress, Boolean* outIsSettable) {
    (void)inDriver;
    (void)inObjectID;
    (void)inClientProcessID;
    if (outIsSettable == NULL || inAddress == NULL) {
        return kAudioHardwareBadPropertySizeError;
    }

    *outIsSettable = false;
    return noErr;
}

static OSStatus STDMETHODCALLTYPE GetPropertyDataSize(AudioServerPlugInDriverRef inDriver, AudioObjectID inObjectID, pid_t inClientProcessID, const AudioObjectPropertyAddress* inAddress, UInt32 inQualifierDataSize, const void* inQualifierData, UInt32* outDataSize) {
    (void)inDriver;
    (void)inClientProcessID;
    (void)inQualifierDataSize;
    (void)inQualifierData;
    if (outDataSize == NULL || inAddress == NULL || !HasProperty(inDriver, inObjectID, inClientProcessID, inAddress)) {
        return kAudioHardwareUnknownPropertyError;
    }

    switch (inAddress->mSelector) {
        case kAudioObjectPropertyName:
        case kAudioObjectPropertyModelName:
        case kAudioObjectPropertyManufacturer:
        case kAudioPlugInPropertyBundleID:
        case kAudioDevicePropertyDeviceUID:
        case kAudioDevicePropertyModelUID:
            *outDataSize = sizeof(CFStringRef);
            return noErr;
        case kAudioObjectPropertyOwnedObjects:
            *outDataSize = (inObjectID == kAudioObjectPlugInObject || inObjectID == kKraspObjectDevice) ? sizeof(AudioObjectID) : 0;
            return noErr;
        case kAudioPlugInPropertyDeviceList:
        case kAudioDevicePropertyStreams:
            *outDataSize = ScopeMatches(inAddress->mScope, kAudioObjectPropertyScopeInput) || inAddress->mScope == kAudioObjectPropertyScopeGlobal ? sizeof(AudioObjectID) : 0;
            return noErr;
        case kAudioObjectPropertyControlList:
            *outDataSize = 0;
            return noErr;
        case kAudioPlugInPropertyResourceBundle:
            *outDataSize = sizeof(CFStringRef);
            return noErr;
        case kAudioDevicePropertyRelatedDevices:
            *outDataSize = sizeof(AudioObjectID);
            return noErr;
        case kAudioDevicePropertyAvailableNominalSampleRates:
            *outDataSize = sizeof(AudioValueRange);
            return noErr;
        case kAudioDevicePropertyBufferFrameSizeRange:
            *outDataSize = sizeof(AudioValueRange);
            return noErr;
        case kAudioDevicePropertyStreamConfiguration:
            *outDataSize = StreamConfigurationSize(ScopeMatches(inAddress->mScope, kAudioObjectPropertyScopeInput) ? KRASP_RING_CHANNELS : 0);
            return noErr;
        case kAudioStreamPropertyVirtualFormat:
        case kAudioStreamPropertyPhysicalFormat:
            *outDataSize = sizeof(AudioStreamBasicDescription);
            return noErr;
        case kAudioStreamPropertyAvailableVirtualFormats:
        case kAudioStreamPropertyAvailablePhysicalFormats:
            *outDataSize = sizeof(AudioStreamRangedDescription);
            return noErr;
        default:
            *outDataSize = sizeof(UInt32);
            return noErr;
    }
}

static OSStatus STDMETHODCALLTYPE GetPropertyData(AudioServerPlugInDriverRef inDriver, AudioObjectID inObjectID, pid_t inClientProcessID, const AudioObjectPropertyAddress* inAddress, UInt32 inQualifierDataSize, const void* inQualifierData, UInt32 inDataSize, UInt32* outDataSize, void* outData) {
    (void)inClientProcessID;
    if (outDataSize == NULL || outData == NULL || inAddress == NULL || !HasProperty(inDriver, inObjectID, inClientProcessID, inAddress)) {
        return kAudioHardwareUnknownPropertyError;
    }

    *outDataSize = 0;

    switch (inAddress->mSelector) {
        case kAudioObjectPropertyBaseClass:
            CopyUInt32(BaseClassForObject(inObjectID), inDataSize, outDataSize, outData);
            return noErr;
        case kAudioObjectPropertyClass:
            CopyUInt32(ClassForObject(inObjectID), inDataSize, outDataSize, outData);
            return noErr;
        case kAudioObjectPropertyOwner:
            CopyObjectID(OwnerForObject(inObjectID), inDataSize, outDataSize, outData);
            return noErr;
        case kAudioObjectPropertyName:
            CopyCFString(inObjectID == kKraspObjectInputStream ? CFSTR("Krasp Microphone Input") : CFSTR("Krasp Microphone"), inDataSize, outDataSize, outData);
            return noErr;
        case kAudioObjectPropertyModelName:
            CopyCFString(CFSTR("Krasp Virtual Microphone"), inDataSize, outDataSize, outData);
            return noErr;
        case kAudioObjectPropertyManufacturer:
            CopyCFString(CFSTR("Krasp"), inDataSize, outDataSize, outData);
            return noErr;
        case kAudioObjectPropertyOwnedObjects:
            if (inObjectID == kAudioObjectPlugInObject) {
                CopyObjectID(kKraspObjectDevice, inDataSize, outDataSize, outData);
            } else if (inObjectID == kKraspObjectDevice) {
                CopyObjectID(kKraspObjectInputStream, inDataSize, outDataSize, outData);
            }
            return noErr;
        case kAudioPlugInPropertyBundleID:
            CopyCFString(CFSTR("io.github.pilshchikov.krasp.hal"), inDataSize, outDataSize, outData);
            return noErr;
        case kAudioPlugInPropertyDeviceList:
            CopyObjectID(kKraspObjectDevice, inDataSize, outDataSize, outData);
            return noErr;
        case kAudioPlugInPropertyTranslateUIDToDevice:
            if (inQualifierDataSize == sizeof(CFStringRef) && inQualifierData != NULL) {
                CFStringRef uid = *((CFStringRef*)inQualifierData);
                CopyObjectID(CFStringCompare(uid, CFSTR("io.github.pilshchikov.krasp.microphone"), 0) == kCFCompareEqualTo ? kKraspObjectDevice : kAudioObjectUnknown, inDataSize, outDataSize, outData);
            } else {
                CopyObjectID(kAudioObjectUnknown, inDataSize, outDataSize, outData);
            }
            return noErr;
        case kAudioPlugInPropertyResourceBundle:
            CopyCFString(CFSTR(""), inDataSize, outDataSize, outData);
            return noErr;
        case kAudioDevicePropertyDeviceUID:
        case kAudioDevicePropertyModelUID:
            CopyCFString(CFSTR("io.github.pilshchikov.krasp.microphone"), inDataSize, outDataSize, outData);
            return noErr;
        case kAudioDevicePropertyTransportType:
            CopyUInt32(kAudioDeviceTransportTypeVirtual, inDataSize, outDataSize, outData);
            return noErr;
        case kAudioDevicePropertyRelatedDevices:
            CopyObjectID(kKraspObjectDevice, inDataSize, outDataSize, outData);
            return noErr;
        case kAudioDevicePropertyClockDomain:
            CopyUInt32(0, inDataSize, outDataSize, outData);
            return noErr;
        case kAudioDevicePropertyDeviceIsAlive:
            CopyUInt32(1, inDataSize, outDataSize, outData);
            return noErr;
        case kAudioDevicePropertyDeviceIsRunning:
            CopyUInt32(gRunningClients > 0 ? 1 : 0, inDataSize, outDataSize, outData);
            return noErr;
        case kAudioDevicePropertyDeviceCanBeDefaultDevice:
            CopyUInt32(ScopeMatches(inAddress->mScope, kAudioObjectPropertyScopeInput) ? 1 : 0, inDataSize, outDataSize, outData);
            return noErr;
        case kAudioDevicePropertyDeviceCanBeDefaultSystemDevice:
            CopyUInt32(0, inDataSize, outDataSize, outData);
            return noErr;
        case kAudioDevicePropertyLatency:
        case kAudioDevicePropertySafetyOffset:
            CopyUInt32(0, inDataSize, outDataSize, outData);
            return noErr;
        case kAudioDevicePropertyStreams:
            if (ScopeMatches(inAddress->mScope, kAudioObjectPropertyScopeInput)) {
                CopyObjectID(kKraspObjectInputStream, inDataSize, outDataSize, outData);
            }
            return noErr;
        case kAudioObjectPropertyControlList:
            *outDataSize = 0;
            return noErr;
        case kAudioDevicePropertyNominalSampleRate:
            CopyFloat64(KRASP_RING_SAMPLE_RATE, inDataSize, outDataSize, outData);
            return noErr;
        case kAudioDevicePropertyAvailableNominalSampleRates:
        case kAudioDevicePropertyBufferFrameSizeRange:
            if (inDataSize >= sizeof(AudioValueRange)) {
                AudioValueRange* range = (AudioValueRange*)outData;
                range->mMinimum = inAddress->mSelector == kAudioDevicePropertyBufferFrameSizeRange ? kKraspBufferFrameSize : KRASP_RING_SAMPLE_RATE;
                range->mMaximum = inAddress->mSelector == kAudioDevicePropertyBufferFrameSizeRange ? kKraspBufferFrameSize : KRASP_RING_SAMPLE_RATE;
                *outDataSize = sizeof(AudioValueRange);
            }
            return noErr;
        case kAudioDevicePropertyIsHidden:
            CopyUInt32(0, inDataSize, outDataSize, outData);
            return noErr;
        case kAudioDevicePropertyBufferFrameSize:
            CopyUInt32(kKraspBufferFrameSize, inDataSize, outDataSize, outData);
            return noErr;
        case kAudioDevicePropertyStreamConfiguration:
            CopyStreamConfiguration(ScopeMatches(inAddress->mScope, kAudioObjectPropertyScopeInput) ? KRASP_RING_CHANNELS : 0, inDataSize, outDataSize, outData);
            return noErr;
        case kAudioDevicePropertyZeroTimeStampPeriod:
            CopyUInt32(kKraspBufferFrameSize, inDataSize, outDataSize, outData);
            return noErr;
        case kAudioStreamPropertyIsActive:
            CopyUInt32(1, inDataSize, outDataSize, outData);
            return noErr;
        case kAudioStreamPropertyDirection:
            CopyUInt32(1, inDataSize, outDataSize, outData);
            return noErr;
        case kAudioStreamPropertyTerminalType:
            CopyUInt32(kAudioStreamTerminalTypeMicrophone, inDataSize, outDataSize, outData);
            return noErr;
        case kAudioStreamPropertyStartingChannel:
            CopyUInt32(1, inDataSize, outDataSize, outData);
            return noErr;
        case kAudioStreamPropertyVirtualFormat:
        case kAudioStreamPropertyPhysicalFormat:
            if (inDataSize >= sizeof(AudioStreamBasicDescription)) {
                *((AudioStreamBasicDescription*)outData) = StreamFormat();
                *outDataSize = sizeof(AudioStreamBasicDescription);
            }
            return noErr;
        case kAudioStreamPropertyAvailableVirtualFormats:
        case kAudioStreamPropertyAvailablePhysicalFormats:
            if (inDataSize >= sizeof(AudioStreamRangedDescription)) {
                *((AudioStreamRangedDescription*)outData) = StreamRangedFormat();
                *outDataSize = sizeof(AudioStreamRangedDescription);
            }
            return noErr;
        default:
            return kAudioHardwareUnknownPropertyError;
    }
}

static OSStatus STDMETHODCALLTYPE SetPropertyData(AudioServerPlugInDriverRef inDriver, AudioObjectID inObjectID, pid_t inClientProcessID, const AudioObjectPropertyAddress* inAddress, UInt32 inQualifierDataSize, const void* inQualifierData, UInt32 inDataSize, const void* inData) {
    (void)inDriver;
    (void)inObjectID;
    (void)inClientProcessID;
    (void)inAddress;
    (void)inQualifierDataSize;
    (void)inQualifierData;
    (void)inDataSize;
    (void)inData;
    return kAudioHardwareUnsupportedOperationError;
}

static OSStatus STDMETHODCALLTYPE StartIO(AudioServerPlugInDriverRef inDriver, AudioObjectID inDeviceObjectID, UInt32 inClientID) {
    (void)inDriver;
    (void)inClientID;
    if (inDeviceObjectID != kKraspObjectDevice) {
        return kAudioHardwareBadObjectError;
    }

    pthread_mutex_lock(&gStateMutex);
    OpenRingIfNeeded();
    if (gRing != NULL) {
        gReadIndex = gRing->writeIndex;
    }
    if (gRunningClients == 0) {
        gSampleTime = 0;
        gZeroHostTime = mach_absolute_time();
        gTimestampSeed++;
    }
    gRunningClients++;
    pthread_mutex_unlock(&gStateMutex);
    return noErr;
}

static OSStatus STDMETHODCALLTYPE StopIO(AudioServerPlugInDriverRef inDriver, AudioObjectID inDeviceObjectID, UInt32 inClientID) {
    (void)inDriver;
    (void)inClientID;
    if (inDeviceObjectID != kKraspObjectDevice) {
        return kAudioHardwareBadObjectError;
    }

    pthread_mutex_lock(&gStateMutex);
    if (gRunningClients > 0) {
        gRunningClients--;
    }
    pthread_mutex_unlock(&gStateMutex);
    return noErr;
}

static OSStatus STDMETHODCALLTYPE GetZeroTimeStamp(AudioServerPlugInDriverRef inDriver, AudioObjectID inDeviceObjectID, UInt32 inClientID, Float64* outSampleTime, UInt64* outHostTime, UInt64* outSeed) {
    (void)inDriver;
    (void)inClientID;
    if (inDeviceObjectID != kKraspObjectDevice || outSampleTime == NULL || outHostTime == NULL || outSeed == NULL) {
        return kAudioHardwareBadObjectError;
    }

    *outSampleTime = gSampleTime;
    *outHostTime = gZeroHostTime;
    *outSeed = gTimestampSeed;
    return noErr;
}

static OSStatus STDMETHODCALLTYPE WillDoIOOperation(AudioServerPlugInDriverRef inDriver, AudioObjectID inDeviceObjectID, UInt32 inClientID, UInt32 inOperationID, Boolean* outWillDo, Boolean* outWillDoInPlace) {
    (void)inDriver;
    (void)inDeviceObjectID;
    (void)inClientID;
    if (outWillDo == NULL || outWillDoInPlace == NULL) {
        return kAudioHardwareBadPropertySizeError;
    }

    *outWillDo = inOperationID == kAudioServerPlugInIOOperationReadInput;
    *outWillDoInPlace = true;
    return noErr;
}

static OSStatus STDMETHODCALLTYPE BeginIOOperation(AudioServerPlugInDriverRef inDriver, AudioObjectID inDeviceObjectID, UInt32 inClientID, UInt32 inOperationID, UInt32 inIOBufferFrameSize, const AudioServerPlugInIOCycleInfo* inIOCycleInfo) {
    (void)inDriver;
    (void)inDeviceObjectID;
    (void)inClientID;
    (void)inOperationID;
    (void)inIOBufferFrameSize;
    (void)inIOCycleInfo;
    return noErr;
}

static OSStatus STDMETHODCALLTYPE DoIOOperation(AudioServerPlugInDriverRef inDriver, AudioObjectID inDeviceObjectID, AudioObjectID inStreamObjectID, UInt32 inClientID, UInt32 inOperationID, UInt32 inIOBufferFrameSize, const AudioServerPlugInIOCycleInfo* inIOCycleInfo, void* ioMainBuffer, void* ioSecondaryBuffer) {
    (void)inDriver;
    (void)inClientID;
    (void)inIOCycleInfo;
    (void)ioSecondaryBuffer;

    if (inDeviceObjectID != kKraspObjectDevice || inStreamObjectID != kKraspObjectInputStream) {
        return kAudioHardwareBadObjectError;
    }

    if (inOperationID == kAudioServerPlugInIOOperationReadInput && ioMainBuffer != NULL) {
        ReadRing((float*)ioMainBuffer, inIOBufferFrameSize);
        gSampleTime += inIOBufferFrameSize;
    }

    return noErr;
}

static OSStatus STDMETHODCALLTYPE EndIOOperation(AudioServerPlugInDriverRef inDriver, AudioObjectID inDeviceObjectID, UInt32 inClientID, UInt32 inOperationID, UInt32 inIOBufferFrameSize, const AudioServerPlugInIOCycleInfo* inIOCycleInfo) {
    (void)inDriver;
    (void)inDeviceObjectID;
    (void)inClientID;
    (void)inOperationID;
    (void)inIOBufferFrameSize;
    (void)inIOCycleInfo;
    return noErr;
}
