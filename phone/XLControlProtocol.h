#pragma once

#include <stdint.h>

static const uint16_t XLControlPort = 6203;
static const uint16_t XLStatusPort = 6204;
static const uint16_t XLFileTransferPort = 6205;
static const uint8_t XLProtocolVersion = 1;
static const uint32_t XLMaxMessagePayload = 1024 * 1024;

static const uint8_t XLControlMagic[4] = {'X', 'L', 'C', '1'};
static const uint8_t XLStatusMagic[4] = {'X', 'L', 'S', '1'};

enum XLMessageType : uint8_t {
    XLMessageHello = 1,
    XLMessageHelloAck = 2,
    XLMessageSystemAction = 11,
    XLMessageRequestKeyframe = 12,
    XLMessageTextInput = 13,
    XLMessageKeyEvent = 14,
    XLMessagePing = 20,
    XLMessagePong = 21,
    XLMessageAck = 22,
    XLMessageError = 23,
    XLMessageDeviceStatus = 30,
    XLMessageVideoStats = 31,
};

enum XLSystemAction : uint16_t {
    XLSystemActionHome = 1,
    XLSystemActionWake = 2,
    XLSystemActionLock = 3,
    XLSystemActionScreenshot = 4,
    XLSystemActionAppSwitcher = 5,
    XLSystemActionControlCenter = 6,
};

enum XLCapability : uint32_t {
    XLCapabilityVideoH264 = 1u << 0,
    XLCapabilitySystemActions = 1u << 2,
    XLCapabilityStatus = 1u << 3,
    XLCapabilityKeyframeRequest = 1u << 4,
    XLCapabilityFileTransfer = 1u << 5,
    XLCapabilityTextInput = 1u << 6,
};

enum XLDeviceStatusFlag : uint16_t {
    XLDeviceStatusScreenOn = 1u << 0,
    XLDeviceStatusUnlocked = 1u << 1,
    XLDeviceStatusCharging = 1u << 2,
    XLDeviceStatusVideoActive = 1u << 3,
};

#pragma pack(push, 1)
typedef struct {
    uint8_t magic[4];
    uint8_t version;
    uint8_t type;
    uint16_t flags;
    uint32_t payloadLength;
    uint32_t sequence;
} XLMessageHeader;

typedef struct {
    uint32_t capabilities;
    uint16_t screenWidth;
    uint16_t screenHeight;
    uint16_t protocolVersion;
    uint16_t reserved;
} XLHelloPayload;

typedef struct {
    uint16_t action;
    uint16_t reserved;
} XLSystemActionPayload;

typedef struct {
    uint32_t page;
    uint32_t usage;
} XLKeyEventPayload;

enum XLKeyModifier : uint16_t {
    XLKeyModifierControl = 1 << 0,
    XLKeyModifierShift = 1 << 1,
    XLKeyModifierAlt = 1 << 2,
    XLKeyModifierGUI = 1 << 3,
};

typedef struct {
    uint64_t monotonicMs;
} XLPingPayload;

typedef struct {
    uint32_t acknowledgedSequence;
    uint32_t resultCode;
} XLAckPayload;

typedef struct {
    uint32_t uptimeSeconds;
    uint16_t batteryPermille;
    uint16_t flags;
    uint16_t videoFpsX10;
    uint16_t videoClients;
    uint32_t droppedFrames;
    uint32_t controlErrors;
} XLDeviceStatusPayload;
#pragma pack(pop)

static_assert(sizeof(XLMessageHeader) == 16, "XLMessageHeader must be 16 bytes");
static_assert(sizeof(XLHelloPayload) == 12, "XLHelloPayload must be 12 bytes");
static_assert(sizeof(XLDeviceStatusPayload) == 20, "XLDeviceStatusPayload must be 20 bytes");
