#pragma once

#include <stdint.h>

static const uint16_t XLVideoPort = 6202;
static const uint16_t XLVideoWidth = 360;
static const uint16_t XLVideoHeight = 640;
static const uint16_t XLVideoFPS = 12;
static const uint32_t XLVideoBitrate = 700000;

enum : uint8_t {
    XLCodecH264 = 1,
};

enum : uint8_t {
    XLPacketVideo = 1,
    XLPacketHeartbeat = 2,
    XLPacketStats = 3,
};

enum : uint8_t {
    XLPacketFlagKeyFrame = 1,
};

typedef struct __attribute__((packed)) {
    uint8_t magic[4];       // XLV3
    uint16_t width;         // network byte order
    uint16_t height;        // network byte order
    uint16_t fps;           // network byte order
    uint8_t codec;          // XLCodecH264
    uint8_t flags;
    uint32_t reserved;
} XLVideoGreeting;

typedef struct __attribute__((packed)) {
    uint8_t type;
    uint8_t flags;
    uint16_t reserved;
    uint32_t payloadLength; // network byte order
    uint32_t sequence;      // network byte order
    uint32_t timestampMs;   // network byte order, monotonic low 32 bits
} XLVideoPacketHeader;

static_assert(sizeof(XLVideoGreeting) == 16, "XLVideoGreeting must be 16 bytes");
static_assert(sizeof(XLVideoPacketHeader) == 16, "XLVideoPacketHeader must be 16 bytes");

