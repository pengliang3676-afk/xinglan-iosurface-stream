#pragma once

#import <Foundation/Foundation.h>

#import "XLControlProtocol.h"

@interface XLHIDSender : NSObject

@property(nonatomic, readonly, getter=isReady) BOOL ready;

- (BOOL)sendTouchPhase:(XLTouchPhase)phase
                 finger:(uint8_t)finger
                      x:(double)x
                      y:(double)y
               pressure:(double)pressure;
- (BOOL)sendHomeButton;
- (BOOL)sendPowerButton;
- (BOOL)sendAppSwitcher;

@end
