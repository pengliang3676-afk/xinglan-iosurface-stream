#import "XLFrameSource.h"
#import "XLPrivateSurface.h"

#import <UIKit/UIKit.h>
#import <VideoToolbox/VideoToolbox.h>
#import <os/lock.h>

extern "C" void CARenderServerRenderDisplay(int display,
                                              CFStringRef displayName,
                                              IOSurfaceRef surface,
                                              int x,
                                              int y);

static const NSInteger XLBufferCount = 3;

static CVPixelBufferRef XLCreateSharedPixelBuffer(size_t width, size_t height) {
    const size_t bytesPerElement = 4;
    const size_t unalignedBytesPerRow = width * bytesPerElement;
    const size_t bytesPerRow = (unalignedBytesPerRow + 63) & ~(size_t)63;
    const size_t allocationSize = bytesPerRow * height;
    NSDictionary *surfaceProperties = @{
        (__bridge NSString *)kIOSurfaceWidth : @(width),
        (__bridge NSString *)kIOSurfaceHeight : @(height),
        (__bridge NSString *)kIOSurfaceBytesPerElement : @(bytesPerElement),
        (__bridge NSString *)kIOSurfaceBytesPerRow : @(bytesPerRow),
        (__bridge NSString *)kIOSurfaceAllocSize : @(allocationSize),
        (__bridge NSString *)kIOSurfacePixelFormat : @(kCVPixelFormatType_32BGRA),
        (__bridge NSString *)kIOSurfaceIsGlobal : @YES,
    };

    IOSurfaceRef surface = IOSurfaceCreate((__bridge CFDictionaryRef)surfaceProperties);
    if (!surface) {
        NSLog(@"[xlstreamd] direct IOSurface create failed: %zux%zu row=%zu",
              width,
              height,
              bytesPerRow);
        return nil;
    }

    NSDictionary *pixelBufferAttributes = @{
        (__bridge NSString *)kCVPixelBufferIOSurfacePropertiesKey : @{},
        (__bridge NSString *)kCVPixelBufferCGImageCompatibilityKey : @YES,
        (__bridge NSString *)kCVPixelBufferCGBitmapContextCompatibilityKey : @YES,
    };
    CVPixelBufferRef pixelBuffer = nil;
    CVReturn result = CVPixelBufferCreateWithIOSurface(
        kCFAllocatorDefault,
        surface,
        (__bridge CFDictionaryRef)pixelBufferAttributes,
        &pixelBuffer);
    CFRelease(surface);
    if (result != kCVReturnSuccess || !pixelBuffer) {
        NSLog(@"[xlstreamd] IOSurface pixel buffer wrap failed: %d", result);
        if (pixelBuffer) CVPixelBufferRelease(pixelBuffer);
        return nil;
    }
    return pixelBuffer;
}

@implementation XLFrameSource {
    CVPixelBufferRef _sourceBuffer;
    CVPixelBufferRef _outputBuffers[XLBufferCount];
    VTPixelTransferSessionRef _transferSession;
    BOOL _busy[XLBufferCount];
    os_unfair_lock _slotLock;
}

- (instancetype)initWithOutputWidth:(size_t)width height:(size_t)height {
    self = [super init];
    if (!self) return nil;

    _outputWidth = width;
    _outputHeight = height;
    _slotLock = OS_UNFAIR_LOCK_INIT;
    memset(_busy, 0, sizeof(_busy));
    memset(_outputBuffers, 0, sizeof(_outputBuffers));

    CGRect nativeBounds = UIScreen.mainScreen.nativeBounds;
    size_t sourceWidth = (size_t)MIN(nativeBounds.size.width, nativeBounds.size.height);
    size_t sourceHeight = (size_t)MAX(nativeBounds.size.width, nativeBounds.size.height);
    if (sourceWidth == 0 || sourceHeight == 0) {
        sourceWidth = 750;
        sourceHeight = 1334;
    }

    _sourceBuffer = XLCreateSharedPixelBuffer(sourceWidth, sourceHeight);
    if (!_sourceBuffer || !CVPixelBufferGetIOSurface(_sourceBuffer)) {
        NSLog(@"[xlstreamd] source IOSurface setup failed");
        return nil;
    }

    for (NSInteger index = 0; index < XLBufferCount; index++) {
        _outputBuffers[index] = XLCreateSharedPixelBuffer(width, height);
        if (!_outputBuffers[index]) {
            NSLog(@"[xlstreamd] output IOSurface %ld setup failed", (long)index);
            return nil;
        }
    }

    OSStatus transferStatus = VTPixelTransferSessionCreate(kCFAllocatorDefault, &_transferSession);
    if (transferStatus != noErr || !_transferSession) {
        NSLog(@"[xlstreamd] pixel transfer session create failed: %d", transferStatus);
        return nil;
    }
    VTSessionSetProperty(_transferSession,
                         kVTPixelTransferPropertyKey_ScalingMode,
                         kVTScalingMode_Trim);
    return self;
}

- (void)dealloc {
    if (_transferSession) CFRelease(_transferSession);
    if (_sourceBuffer) CVPixelBufferRelease(_sourceBuffer);
    for (NSInteger index = 0; index < XLBufferCount; index++) {
        if (_outputBuffers[index]) CVPixelBufferRelease(_outputBuffers[index]);
    }
}

- (NSInteger)acquireSlot {
    os_unfair_lock_lock(&_slotLock);
    NSInteger selected = -1;
    for (NSInteger index = 0; index < XLBufferCount; index++) {
        if (!_busy[index]) {
            _busy[index] = YES;
            selected = index;
            break;
        }
    }
    os_unfair_lock_unlock(&_slotLock);
    return selected;
}

- (void)releaseSlot:(NSInteger)slotIndex {
    if (slotIndex < 0 || slotIndex >= XLBufferCount) return;
    os_unfair_lock_lock(&_slotLock);
    _busy[slotIndex] = NO;
    os_unfair_lock_unlock(&_slotLock);
}

- (BOOL)captureLatestFrame:(CVPixelBufferRef *)pixelBuffer slotIndex:(NSInteger *)slotIndex {
    NSInteger selected = [self acquireSlot];
    if (selected < 0) return NO;

    IOSurfaceRef sourceSurface = CVPixelBufferGetIOSurface(_sourceBuffer);
    if (!sourceSurface) {
        [self releaseSlot:selected];
        return NO;
    }

    CARenderServerRenderDisplay(0, CFSTR("LCD"), sourceSurface, 0, 0);
    OSStatus status = VTPixelTransferSessionTransferImage(_transferSession,
                                                           _sourceBuffer,
                                                           _outputBuffers[selected]);
    if (status != noErr) {
        static dispatch_once_t transferErrorOnce;
        dispatch_once(&transferErrorOnce, ^{
            NSLog(@"[xlstreamd] pixel transfer failed: %d", status);
        });
        [self releaseSlot:selected];
        return NO;
    }

    *pixelBuffer = _outputBuffers[selected];
    *slotIndex = selected;
    return YES;
}

@end
