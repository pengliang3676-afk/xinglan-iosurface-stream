#import "XLHIDSender.h"

#import <dlfcn.h>
#import <mach/mach_time.h>
#include <unistd.h>

typedef CFTypeRef IOHIDEventRef;
typedef CFTypeRef IOHIDEventSystemClientRef;

typedef IOHIDEventSystemClientRef (*XLClientCreateFn)(CFAllocatorRef);
typedef void (*XLDispatchEventFn)(IOHIDEventSystemClientRef, IOHIDEventRef);
typedef void (*XLAppendEventFn)(IOHIDEventRef, IOHIDEventRef, uint32_t);
typedef void (*XLSetIntegerValueFn)(IOHIDEventRef, uint32_t, CFIndex);
typedef void (*XLSetFloatValueFn)(IOHIDEventRef, uint32_t, double);
typedef void (*XLSetSenderIDFn)(IOHIDEventRef, uint64_t);
typedef IOHIDEventRef (*XLDigitizerEventFn)(CFAllocatorRef,
                                            uint64_t,
                                            uint32_t,
                                            uint32_t,
                                            uint32_t,
                                            uint32_t,
                                            uint32_t,
                                            double,
                                            double,
                                            double,
                                            double,
                                            double,
                                            bool,
                                            bool,
                                            uint32_t);
typedef IOHIDEventRef (*XLFingerEventFn)(CFAllocatorRef,
                                        uint64_t,
                                        uint32_t,
                                        uint32_t,
                                        uint32_t,
                                        double,
                                        double,
                                        double,
                                        double,
                                        double,
                                        bool,
                                        bool,
                                        uint32_t);
typedef IOHIDEventRef (*XLKeyboardEventFn)(CFAllocatorRef,
                                          uint64_t,
                                          uint32_t,
                                          uint32_t,
                                          bool,
                                          uint32_t);
typedef IOHIDEventRef (*XLUnicodeEventFn)(CFAllocatorRef,
                                         uint64_t,
                                         const uint8_t *,
                                         uint32_t,
                                         uint32_t,
                                         uint32_t);

static const uint32_t XLDigitizerEventRange = 1u << 0;
static const uint32_t XLDigitizerEventTouch = 1u << 1;
static const uint32_t XLDigitizerEventPosition = 1u << 2;
static const uint32_t XLDigitizerMajorRadius = 0xB0014;
static const uint32_t XLDigitizerMinorRadius = 0xB0015;
static const uint32_t XLDigitizerIsDisplayIntegrated = 0xB0019;
// These are the private digitizer fields used by the iOS HID dispatcher.
// A parent (hand) event must advertise its child collection explicitly;
// otherwise IOHIDEventSystemClientDispatchEvent can accept the event while
// SpringBoard silently ignores it.
static const uint32_t XLEventFieldIsBuiltIn = 0x00000004;
static const uint32_t XLDigitizerEventMask = 0xB0007;
static const uint32_t XLDigitizerRange = 0xB0008;
static const uint32_t XLDigitizerTouch = 0xB0009;
static const uint32_t XLUnicodeEncodingUTF16LE = 1;
// Known-good synthetic sender id used by TrollVNC/iOS HID generators on
// iOS 14.8 through current releases. Without a sender id SpringBoard may
// silently discard an otherwise valid dispatched event.
static const uint64_t XLSyntheticSenderID = 0x8000000817319372ULL;

@implementation XLHIDSender {
    void *_ioKitHandle;
    IOHIDEventSystemClientRef _client;
    XLDispatchEventFn _dispatchEvent;
    XLAppendEventFn _appendEvent;
    XLSetIntegerValueFn _setIntegerValue;
    XLSetFloatValueFn _setFloatValue;
    XLSetSenderIDFn _setSenderID;
    XLDigitizerEventFn _createDigitizerEvent;
    XLFingerEventFn _createFingerEvent;
    XLKeyboardEventFn _createKeyboardEvent;
    XLUnicodeEventFn _createUnicodeEvent;
}

- (instancetype)init {
    self = [super init];
    if (!self) return nil;
    _ioKitHandle = dlopen("/System/Library/Frameworks/IOKit.framework/IOKit", RTLD_NOW);
    if (!_ioKitHandle) return self;

    XLClientCreateFn createClient =
        (XLClientCreateFn)dlsym(_ioKitHandle, "IOHIDEventSystemClientCreate");
    _dispatchEvent =
        (XLDispatchEventFn)dlsym(_ioKitHandle, "IOHIDEventSystemClientDispatchEvent");
    _appendEvent = (XLAppendEventFn)dlsym(_ioKitHandle, "IOHIDEventAppendEvent");
    _setIntegerValue =
        (XLSetIntegerValueFn)dlsym(_ioKitHandle, "IOHIDEventSetIntegerValue");
    _setFloatValue = (XLSetFloatValueFn)dlsym(_ioKitHandle, "IOHIDEventSetFloatValue");
    _setSenderID = (XLSetSenderIDFn)dlsym(_ioKitHandle, "IOHIDEventSetSenderID");
    _createDigitizerEvent =
        (XLDigitizerEventFn)dlsym(_ioKitHandle, "IOHIDEventCreateDigitizerEvent");
    _createFingerEvent =
        (XLFingerEventFn)dlsym(_ioKitHandle, "IOHIDEventCreateDigitizerFingerEvent");
    _createKeyboardEvent =
        (XLKeyboardEventFn)dlsym(_ioKitHandle, "IOHIDEventCreateKeyboardEvent");
    _createUnicodeEvent =
        (XLUnicodeEventFn)dlsym(_ioKitHandle, "IOHIDEventCreateUnicodeEvent");
    if (createClient) _client = createClient(kCFAllocatorDefault);
    return self;
}

- (void)dealloc {
    if (_client) CFRelease(_client);
    if (_ioKitHandle) dlclose(_ioKitHandle);
}

- (BOOL)isReady {
    return _client && _dispatchEvent && _appendEvent && _setIntegerValue &&
        _setFloatValue && _setSenderID && _createDigitizerEvent && _createFingerEvent;
}

- (BOOL)sendTouchPhase:(XLTouchPhase)phase
                 finger:(uint8_t)finger
                      x:(double)x
                      y:(double)y
               pressure:(double)pressure {
    if (!self.ready) return NO;
    x = MAX(0.0, MIN(1.0, x));
    y = MAX(0.0, MIN(1.0, y));
    pressure = MAX(0.0, MIN(1.0, pressure));

    BOOL touching = phase == XLTouchPhaseDown || phase == XLTouchPhaseMove;
    uint32_t childMask = 0;
    switch (phase) {
        case XLTouchPhaseDown:
            childMask = XLDigitizerEventRange | XLDigitizerEventTouch;
            break;
        case XLTouchPhaseMove:
            childMask = XLDigitizerEventPosition;
            break;
        case XLTouchPhaseUp:
            childMask = XLDigitizerEventTouch;
            break;
        case XLTouchPhaseCancel:
            childMask = XLDigitizerEventTouch;
            break;
    }
    uint64_t timestamp = mach_absolute_time();
    uint32_t index = (uint32_t)finger;
    // iOS' digitizer helpers use identity 3 for a finger transducer.  The
    // index remains the caller's finger slot (normally 0).
    uint32_t identity = 3;

    IOHIDEventRef parent = _createDigitizerEvent(kCFAllocatorDefault,
                                                  timestamp,
                                                  3,
                                                  99,
                                                  1,
                                                  0,
                                                  0,
                                                  0,
                                                  0.0,
                                                  0.0,
                                                  0.0,
                                                  0.0,
                                                  false,
                                                  touching,
                                                  0);
    IOHIDEventRef child = _createFingerEvent(kCFAllocatorDefault,
                                              timestamp,
                                              index,
                                              identity,
                                              childMask,
                                              x,
                                              y,
                                              0.0,
                                              pressure,
                                              0.0,
                                              touching,
                                              touching,
                                              0);
    if (!parent || !child) {
        if (parent) CFRelease(parent);
        if (child) CFRelease(child);
        return NO;
    }
    // Mark the collection as an integrated built-in display touch source.
    // These fields are the important difference from a merely well-formed
    // digitizer event: SpringBoard filters out events without them.
    _setIntegerValue(parent, XLDigitizerIsDisplayIntegrated, 1);
    _setIntegerValue(parent, XLEventFieldIsBuiltIn, 1);
    _setFloatValue(child, XLDigitizerMajorRadius, 0.04);
    _setFloatValue(child, XLDigitizerMinorRadius, 0.04);
    _appendEvent(parent, child, 0);
    _setIntegerValue(parent, XLDigitizerEventMask, 0x23);
    _setIntegerValue(parent, XLDigitizerRange, 1);
    _setIntegerValue(parent, XLDigitizerTouch, 1);
    _setSenderID(parent, XLSyntheticSenderID);
    _dispatchEvent(_client, parent);
    CFRelease(child);
    CFRelease(parent);
    return YES;
}

- (BOOL)sendKeyboardPage:(uint32_t)page usage:(uint32_t)usage down:(BOOL)isDown {
    if (!_client || !_dispatchEvent || !_createKeyboardEvent || !_setSenderID) return NO;
    IOHIDEventRef event = _createKeyboardEvent(
        kCFAllocatorDefault, mach_absolute_time(), page, usage, isDown, 0);
    if (!event) return NO;
    _setSenderID(event, XLSyntheticSenderID);
    _dispatchEvent(_client, event);
    CFRelease(event);
    return YES;
}

- (BOOL)sendKeyboardPage:(uint32_t)page usage:(uint32_t)usage {
    return [self sendKeyboardPage:page
                              usage:usage
                          modifiers:(XLKeyModifier)0];
}

- (BOOL)sendKeyboardPage:(uint32_t)page
                   usage:(uint32_t)usage
               modifiers:(XLKeyModifier)modifiers {
    const XLKeyModifier masks[] = {
        XLKeyModifierControl, XLKeyModifierShift, XLKeyModifierAlt, XLKeyModifierGUI
    };
    const uint32_t usages[] = {0xE0, 0xE1, 0xE2, 0xE3};
    NSUInteger pressed = 0;
    for (NSUInteger index = 0; index < 4; index++) {
        if (!(modifiers & masks[index])) continue;
        if (![self sendKeyboardPage:0x07 usage:usages[index] down:YES]) {
            for (NSInteger release = (NSInteger)pressed - 1; release >= 0; release--) {
                NSUInteger prior = (NSUInteger)release;
                if (modifiers & masks[prior]) {
                    [self sendKeyboardPage:0x07 usage:usages[prior] down:NO];
                }
            }
            return NO;
        }
        pressed = index + 1;
    }
    if (modifiers) usleep(8000);
    BOOL sentDown = [self sendKeyboardPage:page usage:usage down:YES];
    usleep(12000);
    BOOL sentUp = sentDown && [self sendKeyboardPage:page usage:usage down:NO];
    for (NSInteger index = 3; index >= 0; index--) {
        NSUInteger release = (NSUInteger)index;
        if (modifiers & masks[release]) {
            if (![self sendKeyboardPage:0x07 usage:usages[release] down:NO]) sentUp = NO;
        }
    }
    return sentDown && sentUp;
}

- (BOOL)sendUnicodeChunk:(NSString *)chunk {
    if (!_client || !_dispatchEvent || !_createUnicodeEvent || !_setIntegerValue ||
        !_setSenderID || chunk.length == 0) {
        return NO;
    }
    NSData *payload = [chunk dataUsingEncoding:NSUTF16LittleEndianStringEncoding];
    if (payload.length == 0 || payload.length > UINT32_MAX) return NO;
    IOHIDEventRef event = _createUnicodeEvent(kCFAllocatorDefault,
                                               mach_absolute_time(),
                                               (const uint8_t *)payload.bytes,
                                               (uint32_t)payload.length,
                                               XLUnicodeEncodingUTF16LE,
                                               0);
    if (!event) return NO;
    // Mark the synthetic event as originating from the built-in input source.
    // This is the same metadata used by the proven touch/keyboard path.
    _setIntegerValue(event, XLEventFieldIsBuiltIn, 1);
    _setSenderID(event, XLSyntheticSenderID);
    _dispatchEvent(_client, event);
    CFRelease(event);
    return YES;
}

- (BOOL)sendUnicodeText:(NSString *)text {
    if (!text.length || !_createUnicodeEvent) return NO;
    __block BOOL sent = YES;
    __block NSMutableString *chunk = [NSMutableString string];
    [text enumerateSubstringsInRange:NSMakeRange(0, text.length)
                              options:NSStringEnumerationByComposedCharacterSequences
                           usingBlock:^(NSString *part,
                                        NSRange substringRange,
                                        NSRange enclosingRange,
                                        BOOL *stop) {
        (void)substringRange;
        (void)enclosingRange;
        if (chunk.length > 0 && chunk.length + part.length > 64) {
            sent = [self sendUnicodeChunk:[chunk copy]];
            [chunk setString:@""];
            if (!sent) {
                *stop = YES;
                return;
            }
            usleep(5000);
        }
        [chunk appendString:part];
    }];
    if (sent && chunk.length > 0) {
        sent = [self sendUnicodeChunk:[chunk copy]];
    }
    return sent;
}

- (BOOL)sendPasteShortcut {
    if (![self sendKeyboardPage:0x07 usage:0xE3 down:YES]) return NO;
    // Give iOS time to establish the Command modifier before sending V. Some
    // third-party editors miss the shortcut when all three events are emitted
    // back-to-back.
    usleep(45000);
    BOOL pasted = [self sendKeyboardPage:0x07 usage:0x19];
    usleep(45000);
    BOOL released = [self sendKeyboardPage:0x07 usage:0xE3 down:NO];
    return pasted && released;
}

- (BOOL)sendHomeButton {
    return [self sendKeyboardPage:0x0C usage:0x40];
}

- (BOOL)sendPowerButton {
    return [self sendKeyboardPage:0x0C usage:0x30];
}

- (BOOL)sendAppSwitcher {
    if (![self sendHomeButton]) return NO;
    usleep(140000);
    return [self sendHomeButton];
}

@end
