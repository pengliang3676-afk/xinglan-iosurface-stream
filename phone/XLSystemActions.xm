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

@interface XLTextInputReceiver : NSObject
@end

static const char *XLScreenWakeNotification = "com.jibeib.xlstream.screen.wake";
static const char *XLScreenLockNotification = "com.jibeib.xlstream.screen.lock";
static CFStringRef const XLTextPasteNotification = CFSTR("com.jibeib.xlstream.text.paste");
static CFStringRef const XLTextDeleteNotification = CFSTR("com.jibeib.xlstream.text.delete");
static CFStringRef const XLTextReturnNotification = CFSTR("com.jibeib.xlstream.text.return");

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
        UIApplication *application = UIApplication.sharedApplication;
        if (CFStringCompare(name, XLTextPasteNotification, 0) == kCFCompareEqualTo) {
            BOOL pasted = [application sendAction:@selector(paste:) to:nil from:nil forEvent:nil];
            if (!pasted) {
                NSString *text = UIPasteboard.generalPasteboard.string ?: @"";
                if (text.length) {
                    [application sendAction:@selector(insertText:) to:nil from:text forEvent:nil];
                }
            }
        } else if (CFStringCompare(name, XLTextDeleteNotification, 0) == kCFCompareEqualTo) {
            [application sendAction:@selector(deleteBackward) to:nil from:nil forEvent:nil];
        } else if (CFStringCompare(name, XLTextReturnNotification, 0) == kCFCompareEqualTo) {
            [application sendAction:@selector(insertText:) to:nil from:@"\n" forEvent:nil];
        }
    });
}

@implementation XLTextInputReceiver

- (instancetype)init {
    self = [super init];
    if (!self) return nil;
    CFNotificationCenterRef center = CFNotificationCenterGetDarwinNotifyCenter();
    NSArray<NSString *> *names = @[
        (__bridge NSString *)XLTextPasteNotification,
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
