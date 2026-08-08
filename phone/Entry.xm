#import <Foundation/Foundation.h>

#import "XLVideoServer.h"

%ctor {
    @autoreleasepool {
        // 等待SpringBoard显示服务稳定后再创建IOSurface和编码器。
        dispatch_after(dispatch_time(DISPATCH_TIME_NOW, 8 * NSEC_PER_SEC),
                       dispatch_get_main_queue(), ^{
            XLStartVideoServer();
        });
    }
}

