#pragma once

#import <CoreVideo/CoreVideo.h>

// iPhoneOS SDK不公开IOSurface框架头文件，但CoreVideo运行时仍提供该接口。
typedef struct __IOSurface *IOSurfaceRef;

#ifdef __cplusplus
extern "C" {
#endif

IOSurfaceRef CVPixelBufferGetIOSurface(CVPixelBufferRef pixelBuffer);

#ifdef __cplusplus
}
#endif

