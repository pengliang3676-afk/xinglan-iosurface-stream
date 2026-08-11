#import <Foundation/Foundation.h>
#import <UIKit/UIKit.h>
#import <notify.h>
#import <objc/message.h>

@interface SBBacklightController : NSObject
+ (instancetype)sharedInstance;
- (void)turnOnScreenFullyWithBacklightSource:(long long)source;
- (void)setBacklightFactor:(float)factor source:(long long)source;
@end

@interface SBLockScreenManager : NSObject
+ (instancetype)sharedInstance;
- (BOOL)unlockUIFromSource:(int)source withOptions:(id)options;
- (void)lockUIFromSource:(int)source withOptions:(id)options;
@end

@interface UIKeyboardImpl : UIResponder
+ (instancetype)activeInstance;
+ (instancetype)sharedInstance;
- (void)insertText:(NSString *)text;
- (void)deleteBackward;
@end

@interface XLTextInputReceiver : NSObject
@property (nonatomic, strong) NSMutableData *pasteBuffer;
@property (nonatomic, assign) uint64_t pasteRequest;
@end

static const char *XLScreenWakeNotification = "com.jibeib.xlstream.screen.wake";
static const char *XLScreenLockNotification = "com.jibeib.xlstream.screen.lock";
static const char *XLControlCenterOpenNotification = "com.jibeib.xlstream.controlcenter.open";
static const char *XLTextInsertNotification = "com.jibeib.xlstream.text.insert";
static const char *XLTextPasteBeginNotification = "com.jibeib.xlstream.text.paste.begin";
static const char *XLTextPasteChunkNotification = "com.jibeib.xlstream.text.paste.chunk";
static const char *XLTextPasteCommitNotification = "com.jibeib.xlstream.text.paste.commit";
static const char *XLTextPasteAckNotification = "com.jibeib.xlstream.text.paste.ack";
static CFStringRef const XLTextDeleteNotification = CFSTR("com.jibeib.xlstream.text.delete");
static CFStringRef const XLTextReturnNotification = CFSTR("com.jibeib.xlstream.text.return");

static UIResponder *XLFirstResponderInView(UIView *view) {
    if (view.isFirstResponder) return view;
    for (UIView *subview in view.subviews) {
        UIResponder *responder = XLFirstResponderInView(subview);
        if (responder) return responder;
    }
    return nil;
}

static UIResponder *XLCurrentFirstResponder(void) {
    UIApplication *application = UIApplication.sharedApplication;
    if (@available(iOS 13.0, *)) {
        for (UIScene *scene in application.connectedScenes) {
            if (![scene isKindOfClass:UIWindowScene.class]) continue;
            UISceneActivationState state = scene.activationState;
            if (state != UISceneActivationStateForegroundActive &&
                state != UISceneActivationStateForegroundInactive) continue;
            for (UIWindow *window in ((UIWindowScene *)scene).windows) {
                UIResponder *responder = XLFirstResponderInView(window);
                if (responder) return responder;
            }
        }
    }
    for (UIWindow *window in application.windows) {
        UIResponder *responder = XLFirstResponderInView(window);
        if (responder) return responder;
    }
    return nil;
}

static UIKeyboardImpl *XLActiveKeyboard(void) {
    Class cls = NSClassFromString(@"UIKeyboardImpl");
    if (!cls) return nil;
    SEL activeSelector = NSSelectorFromString(@"activeInstance");
    if ([cls respondsToSelector:activeSelector]) {
        UIKeyboardImpl *keyboard = ((id (*)(id, SEL))objc_msgSend)(cls, activeSelector);
        if (keyboard) return keyboard;
    }
    SEL sharedSelector = NSSelectorFromString(@"sharedInstance");
    if ([cls respondsToSelector:sharedSelector]) {
        return ((id (*)(id, SEL))objc_msgSend)(cls, sharedSelector);
    }
    return nil;
}

static BOOL XLInsertTextThroughKeyboard(NSString *text) {
    UIKeyboardImpl *keyboard = XLActiveKeyboard();
    if (!keyboard || ![keyboard respondsToSelector:@selector(insertText:)]) return NO;
    [keyboard insertText:text];
    return YES;
}

static BOOL XLInsertTextIntoFocusedControl(NSString *text) {
    if (XLInsertTextThroughKeyboard(text)) return YES;
    UIResponder *responder = XLCurrentFirstResponder();
    SEL selector = @selector(insertText:);
    if (responder && [responder respondsToSelector:selector]) {
        ((void (*)(id, SEL, id))objc_msgSend)(responder, selector, text);
        return YES;
    }
    return [UIApplication.sharedApplication sendAction:selector
                                                     to:nil
                                                   from:text
                                               forEvent:nil];
}

static BOOL XLDeleteFromFocusedControl(void) {
    UIKeyboardImpl *keyboard = XLActiveKeyboard();
    if (keyboard && [keyboard respondsToSelector:@selector(deleteBackward)]) {
        [keyboard deleteBackward];
        return YES;
    }
    UIResponder *responder = XLCurrentFirstResponder();
    SEL selector = @selector(deleteBackward);
    if (responder && [responder respondsToSelector:selector]) {
        ((void (*)(id, SEL))objc_msgSend)(responder, selector);
        return YES;
    }
    return [UIApplication.sharedApplication sendAction:selector
                                                     to:nil
                                                   from:nil
                                               forEvent:nil];
}

static BOOL XLCommitTextIntoFocusedControl(NSString *text) {
    // Direct insertion avoids iOS' paste permission prompt and keeps Ctrl+V
    // independent from the phone clipboard.
    return XLInsertTextIntoFocusedControl(text);
}

static void XLPostPasteAck(uint64_t request, BOOL success) {
    if (request == 0) return;
    int token = 0;
    if (notify_register_check(XLTextPasteAckNotification, &token) != NOTIFY_STATUS_OK) return;
    uint64_t state = (request << 1) | (success ? 1 : 0);
    notify_set_state(token, state);
    notify_post(XLTextPasteAckNotification);
    notify_cancel(token);
}

static id XLShared(Class cls) {
    SEL selector = NSSelectorFromString(@"sharedInstance");
    if (!cls || ![cls respondsToSelector:selector]) return nil;
    return ((id (*)(id, SEL))objc_msgSend)((id)cls, selector);
}

static void XLWakeScreen(void) {
    SBBacklightController *backlight = XLShared(NSClassFromString(@"SBBacklightController"));
    if ([backlight respondsToSelector:@selector(turnOnScreenFullyWithBacklightSource:)]) {
        [backlight turnOnScreenFullyWithBacklightSource:10];
    }
    SBLockScreenManager *lockScreen = XLShared(NSClassFromString(@"SBLockScreenManager"));
    if ([lockScreen respondsToSelector:@selector(unlockUIFromSource:withOptions:)]) {
        NSDictionary *options = @{ @"SBUIUnlockOptionsTurnOnScreenFirstKey": @YES };
        [lockScreen unlockUIFromSource:10 withOptions:options];
    }
}

static void XLLockScreen(void) {
    SBLockScreenManager *lockScreen = XLShared(NSClassFromString(@"SBLockScreenManager"));
    if ([lockScreen respondsToSelector:@selector(lockUIFromSource:withOptions:)]) {
        [lockScreen lockUIFromSource:10 withOptions:nil];
    }
    SBBacklightController *backlight = XLShared(NSClassFromString(@"SBBacklightController"));
    if ([backlight respondsToSelector:@selector(setBacklightFactor:source:)]) {
        [backlight setBacklightFactor:0.0f source:10];
    }
}

static BOOL XLOpenControlCenter(void) {
    id controller = XLShared(NSClassFromString(@"SBControlCenterController"));
    if (!controller) return NO;

    SEL selector = NSSelectorFromString(@"presentAnimated:completion:");
    if ([controller respondsToSelector:selector]) {
        ((void (*)(id, SEL, BOOL, id))objc_msgSend)(
            controller, selector, YES, nil);
        return YES;
    }

    selector = NSSelectorFromString(@"presentAnimated:");
    if ([controller respondsToSelector:selector]) {
        ((void (*)(id, SEL, BOOL))objc_msgSend)(controller, selector, YES);
        return YES;
    }
    return NO;
}

static void XLTextInputNotification(
    CFNotificationCenterRef center,
    void *observer,
    CFStringRef name,
    const void *object,
    CFDictionaryRef userInfo)
{
    (void)center;
    (void)observer;
    (void)object;
    (void)userInfo;
    dispatch_async(dispatch_get_main_queue(), ^{
        if (CFStringCompare(name, XLTextDeleteNotification, 0) == kCFCompareEqualTo) {
            XLDeleteFromFocusedControl();
        } else if (CFStringCompare(name, XLTextReturnNotification, 0) == kCFCompareEqualTo) {
            XLInsertTextIntoFocusedControl(@"\n");
        }
    });
}

@implementation XLTextInputReceiver

- (instancetype)init {
    self = [super init];
    if (!self) return nil;
    CFNotificationCenterRef center = CFNotificationCenterGetDarwinNotifyCenter();
    NSArray<NSString *> *names = @[
        (__bridge NSString *)XLTextDeleteNotification,
        (__bridge NSString *)XLTextReturnNotification,
    ];
    for (NSString *name in names) {
        CFNotificationCenterAddObserver(
            center,
            (__bridge const void *)self,
            XLTextInputNotification,
            (__bridge CFStringRef)name,
            NULL,
            CFNotificationSuspensionBehaviorDeliverImmediately);
    }
    static int insertToken = 0;
    notify_register_dispatch(XLTextInsertNotification, &insertToken,
                             dispatch_get_main_queue(), ^(int token) {
        uint64_t state = 0;
        if (notify_get_state(token, &state) != NOTIFY_STATUS_OK) return;
        NSUInteger length = (NSUInteger)((state >> 56) & 0xFF);
        if (length == 0 || length > 7) return;
        uint8_t bytes[7] = {};
        for (NSUInteger index = 0; index < length; index++) {
            bytes[index] = (uint8_t)((state >> (index * 8)) & 0xFF);
        }
        NSString *text = [[NSString alloc] initWithBytes:bytes
                                                   length:length
                                                 encoding:NSUTF8StringEncoding];
        if (text.length) {
            XLInsertTextIntoFocusedControl(text);
        }
    });
    static int pasteBeginToken = 0;
    static int pasteChunkToken = 0;
    static int pasteCommitToken = 0;
    __weak XLTextInputReceiver *weakSelf = self;
    notify_register_dispatch(XLTextPasteBeginNotification, &pasteBeginToken,
                             dispatch_get_main_queue(), ^(int token) {
        XLTextInputReceiver *receiver = weakSelf;
        uint64_t request = 0;
        notify_get_state(token, &request);
        receiver.pasteRequest = request;
        if (UIApplication.sharedApplication.applicationState == UIApplicationStateActive) {
            receiver.pasteBuffer = [NSMutableData data];
        } else {
            receiver.pasteBuffer = nil;
        }
    });
    notify_register_dispatch(XLTextPasteChunkNotification, &pasteChunkToken,
                             dispatch_get_main_queue(), ^(int token) {
        XLTextInputReceiver *receiver = weakSelf;
        if (!receiver.pasteBuffer) return;
        uint64_t state = 0;
        if (notify_get_state(token, &state) != NOTIFY_STATUS_OK) return;
        NSUInteger length = (NSUInteger)((state >> 56) & 0xFF);
        if (length == 0 || length > 7) {
            receiver.pasteBuffer = nil;
            return;
        }
        uint8_t bytes[7] = {};
        for (NSUInteger index = 0; index < length; index++) {
            bytes[index] = (uint8_t)((state >> (index * 8)) & 0xFF);
        }
        [receiver.pasteBuffer appendBytes:bytes length:length];
    });
    notify_register_dispatch(XLTextPasteCommitNotification, &pasteCommitToken,
                             dispatch_get_main_queue(), ^(int token) {
        (void)token;
        XLTextInputReceiver *receiver = weakSelf;
        NSData *payload = [receiver.pasteBuffer copy];
        receiver.pasteBuffer = nil;
        uint64_t request = receiver.pasteRequest;
        receiver.pasteRequest = 0;
        if (!payload.length ||
            UIApplication.sharedApplication.applicationState != UIApplicationStateActive) {
            XLPostPasteAck(request, NO);
            return;
        }
        NSString *text = [[NSString alloc] initWithData:payload
                                               encoding:NSUTF8StringEncoding];
        if (!text.length) {
            XLPostPasteAck(request, NO);
            return;
        }
        dispatch_after(dispatch_time(DISPATCH_TIME_NOW, 50000000),
                       dispatch_get_main_queue(), ^{
            XLPostPasteAck(request, XLCommitTextIntoFocusedControl(text));
        });
    });
    return self;
}

- (void)dealloc {
    CFNotificationCenterRemoveObserver(
        CFNotificationCenterGetDarwinNotifyCenter(),
        (__bridge const void *)self,
        NULL,
        NULL);
}

@end

static void XLRegisterSpringBoardActions(void) {
    int wakeToken = 0;
    int lockToken = 0;
    int controlCenterToken = 0;
    notify_register_dispatch(XLScreenWakeNotification, &wakeToken,
                             dispatch_get_main_queue(), ^(int token) {
        (void)token;
        XLWakeScreen();
    });
    notify_register_dispatch(XLScreenLockNotification, &lockToken,
                             dispatch_get_main_queue(), ^(int token) {
        (void)token;
        XLLockScreen();
    });
    notify_register_dispatch(XLControlCenterOpenNotification, &controlCenterToken,
                             dispatch_get_main_queue(), ^(int token) {
        (void)token;
        XLOpenControlCenter();
    });
}

%ctor {
    @autoreleasepool {
        NSString *bundleIdentifier = NSBundle.mainBundle.bundleIdentifier ?: @"";
        if ([bundleIdentifier isEqualToString:@"com.apple.springboard"]) {
            dispatch_async(dispatch_get_main_queue(), ^{ XLRegisterSpringBoardActions(); });
        } else {
            static XLTextInputReceiver *receiver;
            receiver = [XLTextInputReceiver new];
        }
    }
}
