#import <Foundation/Foundation.h>
#import <UIKit/UIKit.h>
#import <objc/runtime.h>

#include <arpa/inet.h>
#include <dlfcn.h>
#include <errno.h>
#include <netinet/in.h>
#include <spawn.h>
#include <sys/socket.h>
#include <sys/types.h>
#include <sys/wait.h>
#include <unistd.h>

#import "StreamClient.h"
#import "InputInjector.h"
#import "KeyboardSuppression.h"
#import "Protocol.h"

extern char **environ;

// RootHide does not load a newly-installed LaunchDaemon when the user only
// restarts SpringBoard.  The hook, however, is guaranteed to be loaded by that
// restart.  Keep the small control daemon alive from here as well.  If launchd
// already owns a healthy copy, the loopback readiness check makes this a no-op.
// This deliberately avoids package maintainer scripts, which previously left
// dpkg/Sileo in an inconsistent state when an install was interrupted.
static BOOL IOSPYDaemonPortReady(void) {
    int fd = socket(AF_INET, SOCK_STREAM, 0);
    if (fd < 0) return NO;

    struct timeval timeout = {0, 150 * 1000};
    setsockopt(fd, SOL_SOCKET, SO_SNDTIMEO, &timeout, sizeof(timeout));
    setsockopt(fd, SOL_SOCKET, SO_RCVTIMEO, &timeout, sizeof(timeout));

    struct sockaddr_in address;
    memset(&address, 0, sizeof(address));
    address.sin_family = AF_INET;
    address.sin_port = htons(IOSPY_DEFAULT_PORT);
    address.sin_addr.s_addr = htonl(INADDR_LOOPBACK);
    BOOL ready = connect(fd, (struct sockaddr *)&address, sizeof(address)) == 0;
    close(fd);
    return ready;
}

static NSString *IOSPYBundledDaemonPath(void) {
    Dl_info imageInfo = {};
    if (dladdr((const void *)&IOSPYBundledDaemonPath, &imageInfo) == 0 ||
        !imageInfo.dli_fname) {
        return @"/Applications/XLStream.app/bin/xltouchd";
    }

    NSString *imagePath = [NSString stringWithUTF8String:imageInfo.dli_fname];
    NSRange library = [imagePath rangeOfString:@"/Library/MobileSubstrate/"
                                       options:NSBackwardsSearch];
    if (library.location == NSNotFound) {
        return @"/Applications/XLStream.app/bin/xltouchd";
    }
    NSString *prefix = [imagePath substringToIndex:library.location];
    return [prefix stringByAppendingPathComponent:@"Applications/XLStream.app/bin/xltouchd"];
}

static void IOSPYStartDaemonSupervisor(void) {
    dispatch_async(dispatch_get_global_queue(QOS_CLASS_UTILITY, 0), ^{
        NSString *daemonPath = IOSPYBundledDaemonPath();
        const char *daemon = daemonPath.fileSystemRepresentation;
        NSLog(@"[ioscpyhook] touch daemon supervisor path=%@", daemonPath);

        // A copy loaded by launchd owns the service across userspace restarts.
        // Do this probe only once: repeated probe sockets would accumulate in
        // the listen backlog while the real Windows client holds its session.
        if (IOSPYDaemonPortReady()) {
            NSLog(@"[ioscpyhook] launchd touch daemon already ready");
            return;
        }

        while (1) {
            @autoreleasepool {
                if (![[NSFileManager defaultManager] isExecutableFileAtPath:daemonPath]) {
                    NSLog(@"[ioscpyhook] touch daemon missing: %@", daemonPath);
                    sleep(3);
                    continue;
                }

                pid_t child = -1;
                char *const arguments[] = {const_cast<char *>(daemon), nullptr};
                int result = posix_spawn(&child, daemon, nullptr, nullptr, arguments, environ);
                if (result != 0) {
                    NSLog(@"[ioscpyhook] touch daemon spawn failed: %d", result);
                    sleep(2);
                    continue;
                }

                NSLog(@"[ioscpyhook] touch daemon started pid=%d", child);
                int status = 0;
                while (waitpid(child, &status, 0) < 0 && errno == EINTR) {
                }
                NSLog(@"[ioscpyhook] touch daemon exited status=%d", status);
                sleep(1);
            }
        }
    });
}

// Injected into SpringBoard. Announces itself on load and starts the stream
// client, which connects to the daemon and captures the screen on demand.

// SpringBoard's proxy for an app-requested system alert. Declared so we can
// resolve the cross-app paste confirmation without showing it. We only ever
// message it after confirming the class and selectors at runtime.
@interface SBUserNotificationAlert : NSObject
- (void)_setActivated:(BOOL)activated;
- (void)_sendResponseAndCleanUp:(BOOL)cleanup;
@end

// Only true on iOS 16+, where the blocking "… would like to paste from …" prompt
// exists. iOS 15 (and earlier) never created it, so this stays a clean no-op.
static BOOL gSuppressPasteAlert = NO;

// The cross-app paste confirmation is a SpringBoard-hosted alert presented for
// whatever process read the pasteboard, so suppressing it here covers clipboard
// sync in both directions. We touch ONLY the "pasted" alert; every other
// SpringBoard alert falls through untouched. Any mismatch fails open (the prompt
// just reappears) so a future iOS layout change can't wedge the alert pipe.
%hook SBAlertItem

+ (void)activateAlertItem:(id)arg1 {
    if (gSuppressPasteAlert && arg1) {
        Class cls = NSClassFromString(@"SBUserNotificationAlert");
        if (cls && [arg1 isKindOfClass:cls]) {
            NSString *source = nil;
            Ivar iv = class_getInstanceVariable(object_getClass(arg1), "_alertSource");
            if (iv) {
                @try {
                    source = object_getIvar(arg1, iv);
                } @catch (__unused id e) {
                    source = nil;
                }
            }
            if ([source isKindOfClass:[NSString class]] && [source isEqualToString:@"pasted"] &&
                [arg1 respondsToSelector:@selector(_setActivated:)] &&
                [arg1 respondsToSelector:@selector(_sendResponseAndCleanUp:)]) {
                [arg1 _setActivated:NO];
                [arg1 _sendResponseAndCleanUp:YES];
                return; // swallow it, the alert never appears
            }
        }
    }
    %orig(arg1);
}

%end

%ctor {
    @autoreleasepool {
        NSString *process = [[NSProcessInfo processInfo] processName];
        NSLog(@"[ioscpyhook] loaded into %@ (v0.1.5)", process);

        // Leave a small breadcrumb others can stat to confirm the hook loaded.
        NSString *dir = @"/var/mobile/Library/Preferences";
        NSString *marker = [dir stringByAppendingPathComponent:@"com.ioscpy.hook.loaded"];
        [@"1" writeToFile:marker atomically:YES encoding:NSUTF8StringEncoding error:nil];

        // The blocking paste prompt only exists on iOS 16+, so only arm the
        // suppression there; on iOS 15 it stays inert.
        NSOperatingSystemVersion v = [[NSProcessInfo processInfo] operatingSystemVersion];
        gSuppressPasteAlert = (v.majorVersion >= 16);

        IOSPYOrientationStart();
        IOSPYKeyboardSuppressionInit();
        IOSPYStartDaemonSupervisor();
        [[IOSPYStreamClient shared] start];
    }
}
