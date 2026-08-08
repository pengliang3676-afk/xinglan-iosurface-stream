#pragma once

#import <CoreVideo/CoreVideo.h>
#import <Foundation/Foundation.h>

NS_ASSUME_NONNULL_BEGIN

@interface XLFrameSource : NSObject

@property(nonatomic, readonly) size_t outputWidth;
@property(nonatomic, readonly) size_t outputHeight;

- (instancetype)initWithOutputWidth:(size_t)width height:(size_t)height;

// 返回一个已占用的输出槽；没有空闲槽时直接返回 NO，不排队。
- (BOOL)captureLatestFrame:(CVPixelBufferRef _Nullable * _Nonnull)pixelBuffer
                 slotIndex:(NSInteger *)slotIndex;

// VideoToolbox完成该帧后释放槽位。
- (void)releaseSlot:(NSInteger)slotIndex;

@end

NS_ASSUME_NONNULL_END

