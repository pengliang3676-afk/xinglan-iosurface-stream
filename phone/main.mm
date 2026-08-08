#import <Foundation/Foundation.h>
#import <UIKit/UIKit.h>

#import "XLControlServer.h"
#import "XLStatusServer.h"
#import "XLVideoServer.h"

int main(int argc, char *argv[]) {
    (void)argc;
    (void)argv;
    @autoreleasepool {
        UIDevice.currentDevice.batteryMonitoringEnabled = YES;
        XLStartControlServer();
        XLStartStatusServer();
        // launchd may start before the display server is ready after boot.
        dispatch_after(dispatch_time(DISPATCH_TIME_NOW, 8 * NSEC_PER_SEC),
                       dispatch_get_global_queue(QOS_CLASS_USER_INTERACTIVE, 0), ^{
            XLStartVideoServer();
        });
        dispatch_main();
    }
    return 0;
}
