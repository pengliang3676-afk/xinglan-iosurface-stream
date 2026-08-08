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

    NSDictionary *surfaceAttributes = @{
        (__bridge NSString *)kCVPixelBufferIOSurfacePropertiesKey : @{},
        (__bridge NSString *)kCVPixelBufferCGImageCompatibilityKey : @YES,
        (__bridge NSString *)kCVPixelBufferCGBitmapContextCompatibilityKey : @YES,
    };

    CVReturn result = CVPixelBufferCreate(kCFAllocatorDefault,
                                           sourceWidth,
                                           sourceHeight,
                                           kCVPixelFormatType_32BGRA,
                                           (__bridge CFDictionaryRef)surfaceAttributes,
                                           &_sourceBuffer);
    if (result != kCVReturnSuccess || !_sourceBuffer || !CVPixelBufferGetIOSurface(_sourceBuffer)) {
        NSLog(@"[xlstreamd] source IOSurface create failed: %d", result);
        return nil;
    }

    for (NSInteger index = 0; index < XLBufferCount; index++) {
        result = CVPixelBufferCreate(kCFAllocatorDefault,
                                     width,
                                     height,
                                     kCVPixelFormatType_32BGRA,
                                     (__bridge CFDictionaryRef)surfaceAttributes,
                                     &_outputBuffers[index]);
        if (result != kCVReturnSuccess || !_outputBuffers[index]) {
            NSLog(@"[xlstreamd] output IOSurface %ld create failed: %d", (long)index, result);
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
