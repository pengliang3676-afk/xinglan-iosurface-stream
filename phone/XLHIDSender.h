#pragma once

#import <Foundation/Foundation.h>

#import "XLControlProtocol.h"

@interface XLHIDSender : NSObject

@property(nonatomic, readonly, getter=isReady) BOOL ready;

- (BOOL)sendHomeButton;
- (BOOL)sendPowerButton;
- (BOOL)sendAppSwitcher;
- (BOOL)sendKeyboardPage:(uint32_t)page usage:(uint32_t)usage;
- (BOOL)sendKeyboardPage:(uint32_t)page
                   usage:(uint32_t)usage
               modifiers:(XLKeyModifier)modifiers;
- (BOOL)sendUnicodeText:(NSString *)text;
- (BOOL)sendPasteShortcut;

@end
