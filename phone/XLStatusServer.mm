#import "XLStatusServer.h"

#import "XLControlProtocol.h"
#import "XLControlServer.h"
#import "XLProtocol.h"
#import "XLVideoServer.h"

#import <Foundation/Foundation.h>
#import <UIKit/UIKit.h>

#include <arpa/inet.h>
#include <math.h>
#include <netinet/in.h>
#include <sys/socket.h>
#include <unistd.h>

static BOOL XLStatusWriteAll(int socketHandle, const void *buffer, size_t length) {
    const uint8_t *bytes = (const uint8_t *)buffer;
    size_t offset = 0;
    while (offset < length) {
        ssize_t written = send(socketHandle, bytes + offset, length - offset, MSG_NOSIGNAL);
        if (written <= 0) return NO;
        offset += (size_t)written;
    }
    return YES;
}

static BOOL XLWriteStatusMessage(int client,
                                 XLMessageType type,
                                 uint32_t sequence,
                                 const void *payload,
                                 uint32_t payloadLength) {
    XLMessageHeader header = {};
    memcpy(header.magic, XLStatusMagic, sizeof(header.magic));
    header.version = XLProtocolVersion;
    header.type = type;
    header.payloadLength = htonl(payloadLength);
    header.sequence = htonl(sequence);
    if (!XLStatusWriteAll(client, &header, sizeof(header))) return NO;
    return payloadLength == 0 || XLStatusWriteAll(client, payload, payloadLength);
}

static uint16_t XLBatteryPermille(void) {
    float level = UIDevice.currentDevice.batteryLevel;
    if (level < 0.0f) return 0;
    return (uint16_t)MIN(1000, MAX(0, (int)lrintf(level * 1000.0f)));
}

static uint16_t XLStatusFlags(void) {
    uint16_t flags = XLDeviceStatusScreenOn;
    UIDeviceBatteryState batteryState = UIDevice.currentDevice.batteryState;
    if (batteryState == UIDeviceBatteryStateCharging ||
        batteryState == UIDeviceBatteryStateFull) {
        flags |= XLDeviceStatusCharging;
    }
    if (XLVideoClientCount() > 0) flags |= XLDeviceStatusVideoActive;
    return flags;
}

static void XLHandleStatusClient(int client) {
    @autoreleasepool {
        int enabled = 1;
        setsockopt(client, SOL_SOCKET, SO_NOSIGPIPE, &enabled, sizeof(enabled));
        struct timeval timeout = {2, 0};
        setsockopt(client, SOL_SOCKET, SO_SNDTIMEO, &timeout, sizeof(timeout));
        uint32_t sequence = 1;

        XLHelloPayload hello = {};
        hello.capabilities = htonl(XLCapabilityVideoH264 |
                                   XLCapabilitySystemActions |
                                   XLCapabilityStatus |
                                   XLCapabilityKeyframeRequest |
                                   XLCapabilityFileTransfer);
        hello.screenWidth = htons(XLVideoWidth);
        hello.screenHeight = htons(XLVideoHeight);
        hello.protocolVersion = htons(XLProtocolVersion);
        if (!XLWriteStatusMessage(
                client, XLMessageHello, sequence++, &hello, sizeof(hello))) {
            close(client);
            return;
        }

        while (true) {
            XLDeviceStatusPayload status = {};
            status.uptimeSeconds = htonl((uint32_t)NSProcessInfo.processInfo.systemUptime);
            status.batteryPermille = htons(XLBatteryPermille());
            status.flags = htons(XLStatusFlags());
            status.videoFpsX10 = htons(XLVideoFPS * 10);
            status.videoClients = htons((uint16_t)MIN(UINT16_MAX, XLVideoClientCount()));
            status.droppedFrames = htonl(XLVideoDroppedFrameCount());
            status.controlErrors = htonl(XLControlErrorCount());
            if (!XLWriteStatusMessage(
                    client, XLMessageDeviceStatus, sequence++, &status, sizeof(status))) {
                break;
            }
            sleep(1);
        }
        shutdown(client, SHUT_RDWR);
        close(client);
    }
}

static void XLRunStatusServer(void) {
    int server = socket(AF_INET, SOCK_STREAM, 0);
    if (server < 0) return;
    int enabled = 1;
    setsockopt(server, SOL_SOCKET, SO_REUSEADDR, &enabled, sizeof(enabled));
    struct sockaddr_in address = {};
    address.sin_family = AF_INET;
    address.sin_addr.s_addr = htonl(INADDR_ANY);
    address.sin_port = htons(XLStatusPort);
    if (bind(server, (struct sockaddr *)&address, sizeof(address)) != 0 ||
        listen(server, 4) != 0) {
        close(server);
        return;
    }
    while (true) {
        int client = accept(server, NULL, NULL);
        if (client < 0) continue;
        dispatch_async(dispatch_get_global_queue(QOS_CLASS_UTILITY, 0), ^{
            XLHandleStatusClient(client);
        });
    }
}

void XLStartStatusServer(void) {
    static dispatch_once_t onceToken;
    dispatch_once(&onceToken, ^{
        dispatch_async(dispatch_get_global_queue(QOS_CLASS_UTILITY, 0), ^{
            while (true) {
                XLRunStatusServer();
                sleep(2);
            }
        });
    });
}
