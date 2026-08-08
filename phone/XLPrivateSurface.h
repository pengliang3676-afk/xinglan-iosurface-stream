#pragma once

#import <CoreVideo/CoreVideo.h>

// iPhoneOS SDK不公开IOSurface框架头文件，但CoreVideo运行时仍提供该接口。
typedef struct __IOSurface *IOSurfaceRef;

#ifdef __cplusplus
extern "C" {
#endif

IOSurfaceRef CVPixelBufferGetIOSurface(CVPixelBufferRef pixelBuffer);
CVReturn CVPixelBufferCreateWithIOSurface(CFAllocatorRef allocator,
                                          IOSurfaceRef surface,
                                          CFDictionaryRef pixelBufferAttributes,
                                          CVPixelBufferRef *pixelBufferOut);
IOSurfaceRef IOSurfaceCreate(CFDictionaryRef properties);

extern const CFStringRef kIOSurfaceAllocSize;
extern const CFStringRef kIOSurfaceBytesPerElement;
extern const CFStringRef kIOSurfaceBytesPerRow;
extern const CFStringRef kIOSurfaceHeight;
extern const CFStringRef kIOSurfaceIsGlobal;
extern const CFStringRef kIOSurfacePixelFormat;
extern const CFStringRef kIOSurfaceWidth;

#ifdef __cplusplus
}
#endif
