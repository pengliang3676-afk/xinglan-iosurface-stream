#import "XLControlServer.h"

#import "XLControlProtocol.h"
#import "XLHIDSender.h"
#import "XLProtocol.h"
#import "XLVideoServer.h"

#import <Foundation/Foundation.h>

#include <arpa/inet.h>
#include <atomic>
#include <netinet/in.h>
#include <sys/socket.h>
#include <unistd.h>

static std::atomic_uint XLControlErrors(0);

static BOOL XLReadAll(int socketHandle, void *buffer, size_t length) {
    uint8_t *bytes = (uint8_t *)buffer;
    size_t offset = 0;
    while (offset < length) {
        ssize_t received = recv(socketHandle, bytes + offset, length - offset, 0);
        if (received <= 0) return NO;
        offset += (size_t)received;
    }
    return YES;
}

static BOOL XLControlWriteAll(int socketHandle, const void *buffer, size_t length) {
    const uint8_t *bytes = (const uint8_t *)buffer;
    size_t offset = 0;
    while (offset < length) {
        ssize_t written = send(socketHandle, bytes + offset, length - offset, MSG_NOSIGNAL);
        if (written <= 0) return NO;
        offset += (size_t)written;
    }
    return YES;
}

static BOOL XLWriteControlMessage(int client,
                                  XLMessageType type,
                                  uint32_t sequence,
                                  const void *payload,
                                  uint32_t payloadLength) {
    XLMessageHeader header = {};
    memcpy(header.magic, XLControlMagic, sizeof(header.magic));
    header.version = XLProtocolVersion;
    header.type = type;
    header.flags = htons(0);
    header.payloadLength = htonl(payloadLength);
    header.sequence = htonl(sequence);
    if (!XLControlWriteAll(client, &header, sizeof(header))) return NO;
    return payloadLength == 0 || XLControlWriteAll(client, payload, payloadLength);
}

static BOOL XLWriteAck(int client, uint32_t sequence, uint32_t resultCode) {
    XLAckPayload payload = {};
    payload.acknowledgedSequence = htonl(sequence);
    payload.resultCode = htonl(resultCode);
    return XLWriteControlMessage(client, XLMessageAck, sequence, &payload, sizeof(payload));
}

static BOOL XLWriteHelloAck(int client, uint32_t sequence) {
    XLHelloPayload payload = {};
    payload.capabilities = htonl(XLCapabilityVideoH264 |
                                 XLCapabilityTouch |
                                 XLCapabilitySystemActions |
                                 XLCapabilityStatus |
                                 XLCapabilityKeyframeRequest);
    payload.screenWidth = htons(XLVideoWidth);
    payload.screenHeight = htons(XLVideoHeight);
    payload.protocolVersion = htons(XLProtocolVersion);
    return XLWriteControlMessage(
        client, XLMessageHelloAck, sequence, &payload, sizeof(payload));
}

static uint32_t XLHandleTouch(XLHIDSender *sender, const NSData *data) {
    if (data.length != sizeof(XLTouchPayload)) return 2;
    XLTouchPayload payload = {};
    [data getBytes:&payload length:sizeof(payload)];
    if (payload.phase > XLTouchPhaseCancel) return 3;
    double x = ntohs(payload.x) / 65535.0;
    double y = ntohs(payload.y) / 65535.0;
    double pressure = ntohs(payload.pressure) / 65535.0;
    return [sender sendTouchPhase:(XLTouchPhase)payload.phase
                           finger:payload.finger
                                x:x
                                y:y
                         pressure:pressure] ? 0 : 4;
}

static uint32_t XLHandleSystemAction(XLHIDSender *sender, const NSData *data) {
    if (data.length != sizeof(XLSystemActionPayload)) return 2;
    XLSystemActionPayload payload = {};
    [data getBytes:&payload length:sizeof(payload)];
    switch ((XLSystemAction)ntohs(payload.action)) {
        case XLSystemActionHome:
            return [sender sendHomeButton] ? 0 : 4;
        case XLSystemActionWake:
        case XLSystemActionLock:
            return [sender sendPowerButton] ? 0 : 4;
        case XLSystemActionScreenshot:
            return 5;
    }
    return 3;
}

static void XLHandleControlClient(int client) {
    @autoreleasepool {
        int enabled = 1;
        setsockopt(client, SOL_SOCKET, SO_NOSIGPIPE, &enabled, sizeof(enabled));
        struct timeval timeout = {5, 0};
        setsockopt(client, SOL_SOCKET, SO_RCVTIMEO, &timeout, sizeof(timeout));
        setsockopt(client, SOL_SOCKET, SO_SNDTIMEO, &timeout, sizeof(timeout));
        XLHIDSender *sender = [[XLHIDSender alloc] init];

        while (true) {
            XLMessageHeader header = {};
            if (!XLReadAll(client, &header, sizeof(header))) break;
            uint32_t payloadLength = ntohl(header.payloadLength);
            uint32_t sequence = ntohl(header.sequence);
            if (memcmp(header.magic, XLControlMagic, sizeof(header.magic)) != 0 ||
                header.version != XLProtocolVersion ||
                payloadLength > XLMaxMessagePayload) {
                XLControlErrors.fetch_add(1);
                break;
            }

            NSMutableData *payload = [NSMutableData dataWithLength:payloadLength];
            if (payloadLength > 0 && !XLReadAll(client, payload.mutableBytes, payloadLength)) break;

            switch ((XLMessageType)header.type) {
                case XLMessageHello:
                    if (!XLWriteHelloAck(client, sequence)) return;
                    break;
                case XLMessageTouch: {
                    uint32_t result = XLHandleTouch(sender, payload);
                    if (result != 0) XLControlErrors.fetch_add(1);
                    if (!XLWriteAck(client, sequence, result)) return;
                    break;
                }
                case XLMessageSystemAction: {
                    uint32_t result = XLHandleSystemAction(sender, payload);
                    if (result != 0) XLControlErrors.fetch_add(1);
                    if (!XLWriteAck(client, sequence, result)) return;
                    break;
                }
                case XLMessageRequestKeyframe:
                    XLRequestVideoKeyframe();
                    if (!XLWriteAck(client, sequence, 0)) return;
                    break;
                case XLMessagePing:
                    if (!XLWriteControlMessage(
                            client, XLMessagePong, sequence, payload.bytes, payloadLength)) return;
                    break;
                default:
                    XLControlErrors.fetch_add(1);
                    if (!XLWriteAck(client, sequence, 1)) return;
                    break;
            }
        }
        shutdown(client, SHUT_RDWR);
        close(client);
    }
}

static void XLRunControlServer(void) {
    int server = socket(AF_INET, SOCK_STREAM, 0);
    if (server < 0) return;
    int enabled = 1;
    setsockopt(server, SOL_SOCKET, SO_REUSEADDR, &enabled, sizeof(enabled));
    struct sockaddr_in address = {};
    address.sin_family = AF_INET;
    address.sin_addr.s_addr = htonl(INADDR_ANY);
    address.sin_port = htons(XLControlPort);
    if (bind(server, (struct sockaddr *)&address, sizeof(address)) != 0 ||
        listen(server, 4) != 0) {
        close(server);
        return;
    }
    while (true) {
        int client = accept(server, NULL, NULL);
        if (client < 0) continue;
        dispatch_async(dispatch_get_global_queue(QOS_CLASS_USER_INTERACTIVE, 0), ^{
            XLHandleControlClient(client);
        });
    }
}

void XLStartControlServer(void) {
    static dispatch_once_t onceToken;
    dispatch_once(&onceToken, ^{
        dispatch_async(dispatch_get_global_queue(QOS_CLASS_USER_INTERACTIVE, 0), ^{
            while (true) {
                XLRunControlServer();
                sleep(2);
            }
        });
    });
}

uint32_t XLControlErrorCount(void) {
    return XLControlErrors.load();
}
