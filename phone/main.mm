#import <Foundation/Foundation.h>
#import <UIKit/UIKit.h>

#import "XLControlServer.h"
#import "XLFileTransferServer.h"
#import "XLStatusServer.h"
#import "XLVideoServer.h"

#include <errno.h>
#include <fcntl.h>
#include <sys/file.h>
#include <unistd.h>

namespace {
int gServiceLock = -1;

bool XLAcquireServiceLock(void) {
    const char *lockPath = "/var/mobile/Media/.xlstream-service.lock";
    gServiceLock = open(lockPath, O_CREAT | O_RDWR, 0644);
    if (gServiceLock < 0) {
        NSLog(@"[XLStream] service lock open failed: %d", errno);
        return false;
    }
    if (flock(gServiceLock, LOCK_EX | LOCK_NB) != 0) {
        NSLog(@"[XLStream] another service instance is already running");
        close(gServiceLock);
        gServiceLock = -1;
        return false;
    }
    ftruncate(gServiceLock, 0);
    dprintf(gServiceLock, "%d\n", getpid());
    return true;
}
}  // namespace

int main(int argc, char *argv[]) {
    (void)argc;
    (void)argv;
    @autoreleasepool {
        if (!XLAcquireServiceLock()) {
            return 73;
        }
        UIDevice.currentDevice.batteryMonitoringEnabled = YES;
        XLStartControlServer();
        XLStartStatusServer();
        XLStartFileTransferServer();
        // Give the proven SpringBoard port-6000 plug-in first refusal.  The
        // compatibility listener only fills the gap on phones where that
        // legacy service is absent; it never competes with a healthy copy.
        dispatch_after(dispatch_time(DISPATCH_TIME_NOW, 15 * NSEC_PER_SEC),
                       dispatch_get_global_queue(QOS_CLASS_UTILITY, 0), ^{
            XLStartLegacyControlCompatibilityServer();
        });
        // launchd may start before the display server is ready after boot.
        dispatch_after(dispatch_time(DISPATCH_TIME_NOW, 8 * NSEC_PER_SEC),
                       dispatch_get_global_queue(QOS_CLASS_USER_INTERACTIVE, 0), ^{
            XLStartVideoServer();
        });
        dispatch_main();
    }
    return 0;
}
