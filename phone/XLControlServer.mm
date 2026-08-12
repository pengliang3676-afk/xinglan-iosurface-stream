#import "XLControlServer.h"

#import "XLControlProtocol.h"
#import "XLHIDSender.h"
#import "XLProtocol.h"
#import "XLVideoServer.h"

#import <Foundation/Foundation.h>
#import <UIKit/UIKit.h>
#import <objc/message.h>
#import <objc/runtime.h>

#include <arpa/inet.h>
#include <atomic>
#include <mutex>
#include <netinet/in.h>
#include <notify.h>
#include <sys/socket.h>
#include <netinet/tcp.h>
#include <unistd.h>

static std::atomic_uint XLControlErrors(0);
static const uint16_t XLLegacyControlPort = 6000;

static const char *XLScreenWakeNotification = "com.jibeib.xlstream.screen.wake";
static const char *XLScreenLockNotification = "com.jibeib.xlstream.screen.lock";
static const char *XLControlCenterOpenNotification = "com.jibeib.xlstream.controlcenter.open";
static const char *XLHomeStateRequestNotification = "com.jibeib.xlstream.home.state.request";
static const char *XLHomeStateAckNotification = "com.jibeib.xlstream.home.state.ack";

static std::mutex XLSystemActionMutex;
static BOOL XLLastSystemActionValid = NO;
static uint32_t XLLastSystemActionSequence = 0;
static uint16_t XLLastSystemActionValue = 0;
static uint32_t XLLastSystemActionResult = 0;
static CFAbsoluteTime XLLastSystemActionTime = 0;
static std::atomic_ullong XLHomeStateRequestCounter(0);

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
                                 XLCapabilityKeyframeRequest |
                                 XLCapabilityFileTransfer |
                                 XLCapabilityTextInput |
                                 XLCapabilityTouchStream);
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

static uint32_t XLHandleTextInput(XLHIDSender *sender, const NSData *data) {
    if (data.length == 0 || data.length > XLMaxMessagePayload) return 2;
    NSString *text = [[NSString alloc] initWithData:(NSData *)data encoding:NSUTF8StringEncoding];
    if (!text.length) return 3;
    return [sender sendUnicodeText:text] ? 0 : 4;
}

static uint32_t XLHandleKeyEvent(
    XLHIDSender *sender, const NSData *data, XLKeyModifier modifiers) {
    if (data.length != sizeof(XLKeyEventPayload)) return 2;
    XLKeyEventPayload payload = {};
    [data getBytes:&payload length:sizeof(payload)];
    uint32_t page = ntohl(payload.page);
    uint32_t usage = ntohl(payload.usage);
    return [sender sendKeyboardPage:page
                              usage:usage
                          modifiers:(XLKeyModifier)(modifiers & 0x0F)] ? 0 : 4;
}

static int XLScreenIsOn(void) {
    int token = 0;
    uint64_t state = 0;
    int result = notify_register_check("com.apple.iokit.hid.displayStatus", &token);
    if (result != NOTIFY_STATUS_OK) return -1;
    result = notify_get_state(token, &state);
    notify_cancel(token);
    return result == NOTIFY_STATUS_OK ? (state != 0 ? 1 : 0) : -1;
}

static int XLIsOrdinaryHomeScreen(void) {
    int requestToken = 0;
    int ackToken = 0;
    if (notify_register_check(XLHomeStateRequestNotification, &requestToken) !=
            NOTIFY_STATUS_OK ||
        notify_register_check(XLHomeStateAckNotification, &ackToken) !=
            NOTIFY_STATUS_OK) {
        if (requestToken != 0) notify_cancel(requestToken);
        if (ackToken != 0) notify_cancel(ackToken);
        return -1;
    }

    uint64_t request = XLHomeStateRequestCounter.fetch_add(1) + 1;
    request &= 0x7FFFFFFFFFFFFFFFULL;
    if (request == 0) request = 1;
    // Clear any reply left by a previous daemon process before publishing a
    // new request; otherwise a recycled request number could read a stale
    // SpringBoard answer before the fresh callback runs.
    notify_set_state(ackToken, 0);
    if (notify_set_state(requestToken, request) != NOTIFY_STATUS_OK ||
        notify_post(XLHomeStateRequestNotification) != NOTIFY_STATUS_OK) {
        notify_cancel(requestToken);
        notify_cancel(ackToken);
        return -1;
    }

    int result = -1;
    for (int attempt = 0; attempt < 40; attempt++) {
        uint64_t state = 0;
        if (notify_get_state(ackToken, &state) == NOTIFY_STATUS_OK &&
            (state >> 1) == request) {
            result = (state & 1) != 0 ? 1 : 0;
            break;
        }
        usleep(5000);
    }
    notify_cancel(requestToken);
    notify_cancel(ackToken);
    return result;
}

static uint32_t XLExecuteSystemAction(
    XLHIDSender *sender, XLSystemAction action) {
    switch (action) {
        case XLSystemActionHome:
            // A Home press on an already visible home screen changes pages or
            // can combine with a retry into an unintended double press.
            // SpringBoard is the only process that can reliably tell us that
            // the ordinary home screen is already visible.  If the query is
            // unavailable (for example while SpringBoard is restarting), keep
            // the proven HID fallback.
            if (XLIsOrdinaryHomeScreen() == 1) return 0;
            return [sender sendHomeButton] ? 0 : 4;
        case XLSystemActionWake: {
            int state = XLScreenIsOn();
            notify_post(XLScreenWakeNotification);
            usleep(250000);
            if (XLScreenIsOn() == 1) return 0;
            if (state != 1) {
                if (![sender sendPowerButton]) return 4;
                usleep(350000);
            }
            return [sender sendHomeButton] ? 0 : 4;
        }
        case XLSystemActionLock: {
            int state = XLScreenIsOn();
            notify_post(XLScreenLockNotification);
            usleep(250000);
            if (XLScreenIsOn() == 0) return 0;
            return state == 0 || [sender sendPowerButton] ? 0 : 4;
        }
        case XLSystemActionScreenshot:
            return 5;
        case XLSystemActionAppSwitcher:
            return [sender sendAppSwitcher] ? 0 : 4;
        case XLSystemActionControlCenter:
            return notify_post(XLControlCenterOpenNotification) == NOTIFY_STATUS_OK ? 0 : 4;
    }
    return 3;
}

static uint32_t XLHandleSystemAction(
    XLHIDSender *sender, const NSData *data, uint32_t sequence) {
    if (data.length != sizeof(XLSystemActionPayload)) return 2;
    XLSystemActionPayload payload = {};
    [data getBytes:&payload length:sizeof(payload)];
    uint16_t rawAction = ntohs(payload.action);

    // Keep the cache across control-socket reconnects.  The desktop retains a
    // command until it receives its ACK, so without this guard the phone could
    // execute the command, lose the ACK, reconnect and execute it a second
    // time.  Serialising this short path also closes the two-client race.
    std::lock_guard<std::mutex> guard(XLSystemActionMutex);
    CFAbsoluteTime now = CFAbsoluteTimeGetCurrent();
    if (XLLastSystemActionValid &&
        XLLastSystemActionSequence == sequence &&
        XLLastSystemActionValue == rawAction &&
        now - XLLastSystemActionTime <= 8.0) {
        return XLLastSystemActionResult;
    }

    uint32_t result = XLExecuteSystemAction(sender, (XLSystemAction)rawAction);
    XLLastSystemActionValid = YES;
    XLLastSystemActionSequence = sequence;
    XLLastSystemActionValue = rawAction;
    XLLastSystemActionResult = result;
    XLLastSystemActionTime = CFAbsoluteTimeGetCurrent();
    return result;
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
                case XLMessageTouchStream: {
                    // MOVE is an absolute latest position, not a transaction.
                    // No ACK means USB latency cannot replay old cursor points
                    // after the user has already released the mouse button.
                    uint32_t result = XLHandleTouch(sender, payload);
                    if (result != 0) XLControlErrors.fetch_add(1);
                    break;
                }
                case XLMessageSystemAction: {
                    uint32_t result = XLHandleSystemAction(sender, payload, sequence);
                    if (result != 0) XLControlErrors.fetch_add(1);
                    if (!XLWriteAck(client, sequence, result)) return;
                    break;
                }
                case XLMessageTextInput: {
                    uint32_t result = XLHandleTextInput(sender, payload);
                    if (result != 0) XLControlErrors.fetch_add(1);
                    if (!XLWriteAck(client, sequence, result)) return;
                    break;
                }
                case XLMessageKeyEvent: {
                    uint32_t result = XLHandleKeyEvent(
                        sender, payload, (XLKeyModifier)ntohs(header.flags));
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
        int noDelay = 1;
        setsockopt(client, IPPROTO_TCP, TCP_NODELAY, &noDelay, sizeof(noDelay));
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

static void XLHandleLegacyLine(XLHIDSender *sender, NSString *line) {
    if ([line isEqualToString:@"13"]) {
        [sender sendHomeButton];
        return;
    }
    if ([line isEqualToString:@"14"]) {
        int state = XLScreenIsOn();
        if (state != 1) {
            [sender sendPowerButton];
            usleep(350000);
        }
        [sender sendHomeButton];
        return;
    }
    if ([line isEqualToString:@"15"]) {
        if (XLScreenIsOn() != 0) [sender sendPowerButton];
        return;
    }
    if ([line isEqualToString:@"16"]) {
        [sender sendAppSwitcher];
    }
}

static void XLHandleLegacyControlClient(int client) {
    @autoreleasepool {
        int enabled = 1;
        setsockopt(client, SOL_SOCKET, SO_NOSIGPIPE, &enabled, sizeof(enabled));
        struct timeval timeout = {5, 0};
        setsockopt(client, SOL_SOCKET, SO_RCVTIMEO, &timeout, sizeof(timeout));
        XLHIDSender *sender = [[XLHIDSender alloc] init];
        NSMutableData *pending = [NSMutableData data];
        uint8_t buffer[2048];
        while (true) {
            ssize_t received = recv(client, buffer, sizeof(buffer), 0);
            if (received <= 0) break;
            [pending appendBytes:buffer length:(NSUInteger)received];
            while (pending.length >= 2) {
                const uint8_t *bytes = (const uint8_t *)pending.bytes;
                NSRange delimiter = NSMakeRange(NSNotFound, 0);
                for (NSUInteger index = 0; index + 1 < pending.length; index++) {
                    if (bytes[index] == '\r' && bytes[index + 1] == '\n') {
                        delimiter = NSMakeRange(index, 2);
                        break;
                    }
                }
                if (delimiter.location == NSNotFound) {
                    if (pending.length > 4096) [pending setLength:0];
                    break;
                }
                NSData *lineData = [pending subdataWithRange:
                    NSMakeRange(0, delimiter.location)];
                [pending replaceBytesInRange:
                    NSMakeRange(0, NSMaxRange(delimiter)) withBytes:NULL length:0];
                NSString *line = [[NSString alloc] initWithData:lineData
                                                       encoding:NSUTF8StringEncoding];
                if (line.length) XLHandleLegacyLine(sender, line);
            }
        }
        shutdown(client, SHUT_RDWR);
        close(client);
    }
}

static void XLRunLegacyControlCompatibilityServer(void) {
    int server = socket(AF_INET, SOCK_STREAM, 0);
    if (server < 0) return;
    int enabled = 1;
    setsockopt(server, SOL_SOCKET, SO_REUSEADDR, &enabled, sizeof(enabled));
    struct sockaddr_in address = {};
    address.sin_family = AF_INET;
    address.sin_addr.s_addr = htonl(INADDR_ANY);
    address.sin_port = htons(XLLegacyControlPort);
    // A bind failure means the original SpringBoard service is healthy.  Do
    // not retry later and steal its port during a respring window.
    if (bind(server, (struct sockaddr *)&address, sizeof(address)) != 0 ||
        listen(server, 16) != 0) {
        close(server);
        return;
    }
    NSLog(@"[xlstreamd] legacy control compatibility listening on port %u",
          XLLegacyControlPort);
    while (true) {
        int client = accept(server, NULL, NULL);
        if (client < 0) continue;
        dispatch_async(dispatch_get_global_queue(QOS_CLASS_USER_INTERACTIVE, 0), ^{
            XLHandleLegacyControlClient(client);
        });
    }
}

void XLStartLegacyControlCompatibilityServer(void) {
    static dispatch_once_t onceToken;
    dispatch_once(&onceToken, ^{
        dispatch_async(dispatch_get_global_queue(QOS_CLASS_UTILITY, 0), ^{
            XLRunLegacyControlCompatibilityServer();
        });
    });
}
