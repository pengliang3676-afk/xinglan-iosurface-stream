#import "XLHIDSender.h"

#import <dlfcn.h>
#import <mach/mach_time.h>

typedef CFTypeRef IOHIDEventRef;
typedef CFTypeRef IOHIDEventSystemClientRef;

typedef IOHIDEventSystemClientRef (*XLClientCreateFn)(CFAllocatorRef);
typedef void (*XLDispatchEventFn)(IOHIDEventSystemClientRef, IOHIDEventRef);
typedef void (*XLAppendEventFn)(IOHIDEventRef, IOHIDEventRef, uint32_t);
typedef void (*XLSetIntegerValueFn)(IOHIDEventRef, uint32_t, CFIndex);
typedef void (*XLSetFloatValueFn)(IOHIDEventRef, uint32_t, double);
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

static const uint32_t XLDigitizerEventRange = 1u << 0;
static const uint32_t XLDigitizerEventTouch = 1u << 1;
static const uint32_t XLDigitizerEventPosition = 1u << 2;
static const uint32_t XLDigitizerMajorRadius = 0xB0014;
static const uint32_t XLDigitizerMinorRadius = 0xB0015;
static const uint32_t XLDigitizerIsDisplayIntegrated = 0xB0019;

@implementation XLHIDSender {
    void *_ioKitHandle;
    IOHIDEventSystemClientRef _client;
    XLDispatchEventFn _dispatchEvent;
    XLAppendEventFn _appendEvent;
    XLSetIntegerValueFn _setIntegerValue;
    XLSetFloatValueFn _setFloatValue;
    XLDigitizerEventFn _createDigitizerEvent;
    XLFingerEventFn _createFingerEvent;
    XLKeyboardEventFn _createKeyboardEvent;
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
    _createDigitizerEvent =
        (XLDigitizerEventFn)dlsym(_ioKitHandle, "IOHIDEventCreateDigitizerEvent");
    _createFingerEvent =
        (XLFingerEventFn)dlsym(_ioKitHandle, "IOHIDEventCreateDigitizerFingerEvent");
    _createKeyboardEvent =
        (XLKeyboardEventFn)dlsym(_ioKitHandle, "IOHIDEventCreateKeyboardEvent");
    if (createClient) _client = createClient(kCFAllocatorDefault);
    return self;
}

- (void)dealloc {
    if (_client) CFRelease(_client);
    if (_ioKitHandle) dlclose(_ioKitHandle);
}

- (BOOL)isReady {
    return _client && _dispatchEvent && _appendEvent && _setIntegerValue &&
        _setFloatValue && _createDigitizerEvent && _createFingerEvent;
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
    uint32_t childMask = phase == XLTouchPhaseMove
        ? XLDigitizerEventPosition
        : (XLDigitizerEventTouch | XLDigitizerEventRange);
    uint32_t parentMask = phase == XLTouchPhaseMove ? 0 : XLDigitizerEventTouch;
    uint64_t timestamp = mach_absolute_time();
    uint32_t identity = MAX((uint32_t)1, (uint32_t)finger + 1);

    IOHIDEventRef parent = _createDigitizerEvent(kCFAllocatorDefault,
                                                  timestamp,
                                                  3,
                                                  0,
                                                  0,
                                                  parentMask,
                                                  0,
                                                  0.0,
                                                  0.0,
                                                  0.0,
                                                  0.0,
                                                  0.0,
                                                  false,
                                                  touching,
                                                  0);
    IOHIDEventRef child = _createFingerEvent(kCFAllocatorDefault,
                                              timestamp,
                                              identity,
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
    _setIntegerValue(parent, XLDigitizerIsDisplayIntegrated, 1);
    _setIntegerValue(child, XLDigitizerIsDisplayIntegrated, 1);
    _setFloatValue(child, XLDigitizerMajorRadius, 0.04);
    _setFloatValue(child, XLDigitizerMinorRadius, 0.04);
    _appendEvent(parent, child, 0);
    _dispatchEvent(_client, parent);
    CFRelease(child);
    CFRelease(parent);
    return YES;
}

- (BOOL)sendKeyboardPage:(uint32_t)page usage:(uint32_t)usage {
    if (!_client || !_dispatchEvent || !_createKeyboardEvent) return NO;
    IOHIDEventRef down = _createKeyboardEvent(
        kCFAllocatorDefault, mach_absolute_time(), page, usage, true, 0);
    IOHIDEventRef up = _createKeyboardEvent(
        kCFAllocatorDefault, mach_absolute_time(), page, usage, false, 0);
    if (!down || !up) {
        if (down) CFRelease(down);
        if (up) CFRelease(up);
        return NO;
    }
    _dispatchEvent(_client, down);
    _dispatchEvent(_client, up);
    CFRelease(down);
    CFRelease(up);
    return YES;
}

- (BOOL)sendHomeButton {
    return [self sendKeyboardPage:0x0C usage:0x40];
}

- (BOOL)sendPowerButton {
    return [self sendKeyboardPage:0x0C usage:0x30];
}

@end
