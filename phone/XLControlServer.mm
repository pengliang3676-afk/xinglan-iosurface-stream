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
#include <netinet/in.h>
#include <notify.h>
#include <sys/socket.h>
#include <unistd.h>

static std::atomic_uint XLControlErrors(0);

static const char *XLScreenWakeNotification = "com.jibeib.xlstream.screen.wake";
static const char *XLScreenLockNotification = "com.jibeib.xlstream.screen.lock";
static const char *XLTextInsertNotification = "com.jibeib.xlstream.text.insert";
static const char *XLTextPasteBeginNotification = "com.jibeib.xlstream.text.paste.begin";
static const char *XLTextPasteChunkNotification = "com.jibeib.xlstream.text.paste.chunk";
static const char *XLTextPasteCommitNotification = "com.jibeib.xlstream.text.paste.commit";
static const char *XLTextPasteAckNotification = "com.jibeib.xlstream.text.paste.ack";
static const char *XLTextDeleteNotification = "com.jibeib.xlstream.text.delete";
static const char *XLTextReturnNotification = "com.jibeib.xlstream.text.return";

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
                                 XLCapabilityTextInput);
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

static BOOL XLPostTextScalars(NSString *text) {
    int token = 0;
    if (notify_register_check(XLTextInsertNotification, &token) != NOTIFY_STATUS_OK) {
        return NO;
    }
    BOOL sent = YES;
    for (NSUInteger index = 0; index < text.length;) {
        NSRange range = [text rangeOfComposedCharacterSequenceAtIndex:index];
        NSString *part = [text substringWithRange:range];
        NSData *utf8 = [part dataUsingEncoding:NSUTF8StringEncoding];
        // A composed emoji can exceed seven bytes. Split it at Unicode scalar
        // boundaries so every notification fits into notify's 64-bit state.
        if (utf8.length > 7) {
            unichar first = [text characterAtIndex:index];
            range.length = (CFStringIsSurrogateHighCharacter(first) &&
                            index + 1 < text.length &&
                            CFStringIsSurrogateLowCharacter([text characterAtIndex:index + 1])) ? 2 : 1;
            part = [text substringWithRange:range];
            utf8 = [part dataUsingEncoding:NSUTF8StringEncoding];
        }
        if (utf8.length == 0 || utf8.length > 7) {
            sent = NO;
            break;
        }
        uint64_t state = ((uint64_t)utf8.length << 56);
        const uint8_t *bytes = (const uint8_t *)utf8.bytes;
        for (NSUInteger byteIndex = 0; byteIndex < utf8.length; byteIndex++) {
            state |= ((uint64_t)bytes[byteIndex] << (byteIndex * 8));
        }
        if (notify_set_state(token, state) != NOTIFY_STATUS_OK ||
            notify_post(XLTextInsertNotification) != NOTIFY_STATUS_OK) {
            sent = NO;
            break;
        }
        // Prevent Darwin notifications for consecutive characters from being
        // coalesced before the foreground app consumes the shared state.
        usleep(20000);
        index = NSMaxRange(range);
    }
    notify_cancel(token);
    return sent;
}

static BOOL XLPostPasteText(NSString *text) {
    NSData *utf8 = [text dataUsingEncoding:NSUTF8StringEncoding];
    if (utf8.length == 0 || utf8.length > XLMaxMessagePayload) return NO;
    static std::atomic_uint_fast64_t nextRequest(1);
    uint64_t request = nextRequest.fetch_add(1);
    request &= 0x7FFFFFFFFFFFFFFFULL;
    if (request == 0) request = nextRequest.fetch_add(1) & 0x7FFFFFFFFFFFFFFFULL;
    int beginToken = 0;
    int chunkToken = 0;
    int ackToken = 0;
    if (notify_register_check(XLTextPasteBeginNotification, &beginToken) != NOTIFY_STATUS_OK ||
        notify_register_check(XLTextPasteChunkNotification, &chunkToken) != NOTIFY_STATUS_OK ||
        notify_register_check(XLTextPasteAckNotification, &ackToken) != NOTIFY_STATUS_OK) {
        if (beginToken) notify_cancel(beginToken);
        if (chunkToken) notify_cancel(chunkToken);
        if (ackToken) notify_cancel(ackToken);
        return NO;
    }
    BOOL sent = notify_set_state(beginToken, request) == NOTIFY_STATUS_OK &&
                notify_post(XLTextPasteBeginNotification) == NOTIFY_STATUS_OK;
    usleep(30000);
    const uint8_t *bytes = (const uint8_t *)utf8.bytes;
    for (NSUInteger offset = 0; sent && offset < utf8.length; offset += 7) {
        NSUInteger length = MIN((NSUInteger)7, utf8.length - offset);
        uint64_t state = ((uint64_t)length << 56);
        for (NSUInteger index = 0; index < length; index++) {
            state |= ((uint64_t)bytes[offset + index] << (index * 8));
        }
        sent = notify_set_state(chunkToken, state) == NOTIFY_STATUS_OK &&
               notify_post(XLTextPasteChunkNotification) == NOTIFY_STATUS_OK;
        // Keep consecutive state updates from being coalesced before the
        // foreground app has copied each chunk.
        usleep(30000);
    }
    if (sent) {
        sent = notify_post(XLTextPasteCommitNotification) == NOTIFY_STATUS_OK;
    }
    BOOL applied = NO;
    for (NSUInteger attempt = 0; sent && attempt < 75; attempt++) {
        uint64_t ackState = 0;
        if (notify_get_state(ackToken, &ackState) == NOTIFY_STATUS_OK &&
            (ackState >> 1) == request) {
            applied = (ackState & 1) != 0;
            break;
        }
        usleep(10000);
    }
    notify_cancel(beginToken);
    notify_cancel(chunkToken);
    notify_cancel(ackToken);
    return sent && applied;
}

static uint32_t XLHandleTextInput(XLHIDSender *sender, const NSData *data) {
    if (data.length == 0 || data.length > XLMaxMessagePayload) return 2;
    NSString *text = [[NSString alloc] initWithData:(NSData *)data encoding:NSUTF8StringEncoding];
    if (!text.length) return 3;
    // Only acknowledge success after the foreground app confirms that it
    // applied the text through its active iOS keyboard/input responder.
    (void)sender;
    return XLPostPasteText(text) ? 0 : 4;
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

static uint32_t XLHandleSystemAction(XLHIDSender *sender, const NSData *data) {
    if (data.length != sizeof(XLSystemActionPayload)) return 2;
    XLSystemActionPayload payload = {};
    [data getBytes:&payload length:sizeof(payload)];
    switch ((XLSystemAction)ntohs(payload.action)) {
        case XLSystemActionHome:
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
