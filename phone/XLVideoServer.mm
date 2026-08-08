#import "XLVideoServer.h"

#import "XLFrameSource.h"
#import "XLProtocol.h"

#import <CoreMedia/CoreMedia.h>
#import <Foundation/Foundation.h>
#import <VideoToolbox/VideoToolbox.h>

#include <arpa/inet.h>
#include <atomic>
#include <mach/mach_time.h>
#include <netinet/in.h>
#include <sys/socket.h>
#include <unistd.h>

typedef struct {
    int socketHandle;
    std::atomic_bool connected;
    std::atomic_uint sequence;
} XLConnectionContext;

typedef struct {
    void *frameSource;
    NSInteger slotIndex;
} XLFrameToken;

static BOOL XLWriteAll(int socketHandle, const void *buffer, size_t length) {
    const uint8_t *bytes = (const uint8_t *)buffer;
    size_t offset = 0;
    while (offset < length) {
        ssize_t written = send(socketHandle,
                               bytes + offset,
                               length - offset,
                               MSG_NOSIGNAL);
        if (written <= 0) return NO;
        offset += (size_t)written;
    }
    return YES;
}

static void XLAppendStartCode(NSMutableData *data) {
    static const uint8_t startCode[] = {0, 0, 0, 1};
    [data appendBytes:startCode length:sizeof(startCode)];
}

static uint32_t XLMonotonicMilliseconds(void) {
    static mach_timebase_info_data_t timebase;
    static dispatch_once_t onceToken;
    dispatch_once(&onceToken, ^{ mach_timebase_info(&timebase); });
    uint64_t nanoseconds = mach_continuous_time() * timebase.numer / timebase.denom;
    return (uint32_t)(nanoseconds / 1000000ULL);
}

static void XLReleaseFrameToken(XLFrameToken *token) {
    if (!token) return;
    XLFrameSource *source = (__bridge XLFrameSource *)token->frameSource;
    [source releaseSlot:token->slotIndex];
    delete token;
}

static void XLEncoderCallback(void *outputCallbackRefCon,
                              void *sourceFrameRefCon,
                              OSStatus status,
                              VTEncodeInfoFlags infoFlags,
                              CMSampleBufferRef sampleBuffer) {
    (void)infoFlags;
    XLFrameToken *token = (XLFrameToken *)sourceFrameRefCon;
    XLConnectionContext *context = (XLConnectionContext *)outputCallbackRefCon;

    if (!context || !context->connected.load() || status != noErr ||
        !sampleBuffer || !CMSampleBufferDataIsReady(sampleBuffer)) {
        XLReleaseFrameToken(token);
        return;
    }

    @autoreleasepool {
        NSMutableData *payload = [NSMutableData data];
        CFArrayRef attachments = CMSampleBufferGetSampleAttachmentsArray(sampleBuffer, false);
        BOOL keyFrame = YES;
        if (attachments && CFArrayGetCount(attachments) > 0) {
            CFDictionaryRef attachment = (CFDictionaryRef)CFArrayGetValueAtIndex(attachments, 0);
            keyFrame = !CFDictionaryContainsKey(attachment, kCMSampleAttachmentKey_NotSync);
        }

        if (keyFrame) {
            CMFormatDescriptionRef format = CMSampleBufferGetFormatDescription(sampleBuffer);
            for (size_t index = 0; index < 2; index++) {
                const uint8_t *parameter = NULL;
                size_t parameterSize = 0;
                size_t parameterCount = 0;
                int headerLength = 0;
                if (CMVideoFormatDescriptionGetH264ParameterSetAtIndex(format,
                                                                        index,
                                                                        &parameter,
                                                                        &parameterSize,
                                                                        &parameterCount,
                                                                        &headerLength) == noErr) {
                    XLAppendStartCode(payload);
                    [payload appendBytes:parameter length:parameterSize];
                }
            }
        }

        CMBlockBufferRef block = CMSampleBufferGetDataBuffer(sampleBuffer);
        size_t totalLength = 0;
        char *dataPointer = NULL;
        if (block && CMBlockBufferGetDataPointer(block, 0, NULL, &totalLength, &dataPointer) == kCMBlockBufferNoErr) {
            size_t offset = 0;
            while (offset + 4 <= totalLength) {
                uint32_t nalLength = 0;
                memcpy(&nalLength, dataPointer + offset, sizeof(nalLength));
                nalLength = CFSwapInt32BigToHost(nalLength);
                offset += 4;
                if (nalLength == 0 || offset + nalLength > totalLength) break;
                XLAppendStartCode(payload);
                [payload appendBytes:dataPointer + offset length:nalLength];
                offset += nalLength;
            }
        }

        if (payload.length > 0 && payload.length <= UINT32_MAX) {
            XLVideoPacketHeader header = {};
            header.type = XLPacketVideo;
            header.flags = keyFrame ? XLPacketFlagKeyFrame : 0;
            header.payloadLength = htonl((uint32_t)payload.length);
            header.sequence = htonl(context->sequence.fetch_add(1));
            header.timestampMs = htonl(XLMonotonicMilliseconds());
            if (!XLWriteAll(context->socketHandle, &header, sizeof(header)) ||
                !XLWriteAll(context->socketHandle, payload.bytes, payload.length)) {
                context->connected.store(false);
            }
        }
    }

    XLReleaseFrameToken(token);
}

static VTCompressionSessionRef XLCreateEncoder(XLConnectionContext *context) {
    VTCompressionSessionRef encoder = NULL;
    OSStatus status = VTCompressionSessionCreate(kCFAllocatorDefault,
                                                  XLVideoWidth,
                                                  XLVideoHeight,
                                                  kCMVideoCodecType_H264,
                                                  NULL,
                                                  NULL,
                                                  NULL,
                                                  XLEncoderCallback,
                                                  context,
                                                  &encoder);
    if (status != noErr || !encoder) return NULL;

    VTSessionSetProperty(encoder, kVTCompressionPropertyKey_RealTime, kCFBooleanTrue);
    VTSessionSetProperty(encoder, kVTCompressionPropertyKey_AllowFrameReordering, kCFBooleanFalse);
    VTSessionSetProperty(encoder,
                         kVTCompressionPropertyKey_ProfileLevel,
                         kVTProfileLevel_H264_Baseline_AutoLevel);

    int fps = XLVideoFPS;
    int bitrate = XLVideoBitrate;
    int keyInterval = XLVideoFPS * 2;
    CFNumberRef fpsValue = CFNumberCreate(kCFAllocatorDefault, kCFNumberIntType, &fps);
    CFNumberRef bitrateValue = CFNumberCreate(kCFAllocatorDefault, kCFNumberIntType, &bitrate);
    CFNumberRef keyValue = CFNumberCreate(kCFAllocatorDefault, kCFNumberIntType, &keyInterval);
    VTSessionSetProperty(encoder, kVTCompressionPropertyKey_ExpectedFrameRate, fpsValue);
    VTSessionSetProperty(encoder, kVTCompressionPropertyKey_AverageBitRate, bitrateValue);
    VTSessionSetProperty(encoder, kVTCompressionPropertyKey_MaxKeyFrameInterval, keyValue);
    CFRelease(fpsValue);
    CFRelease(bitrateValue);
    CFRelease(keyValue);
    VTCompressionSessionPrepareToEncodeFrames(encoder);
    return encoder;
}

static BOOL XLSendGreeting(int client) {
    XLVideoGreeting greeting = {};
    memcpy(greeting.magic, "XLV3", 4);
    greeting.width = htons(XLVideoWidth);
    greeting.height = htons(XLVideoHeight);
    greeting.fps = htons(XLVideoFPS);
    greeting.codec = XLCodecH264;
    return XLWriteAll(client, &greeting, sizeof(greeting));
}

static void XLHandleVideoClient(int client) {
    @autoreleasepool {
        int enabled = 1;
        setsockopt(client, SOL_SOCKET, SO_NOSIGPIPE, &enabled, sizeof(enabled));
        int sendBuffer = 256 * 1024;
        setsockopt(client, SOL_SOCKET, SO_SNDBUF, &sendBuffer, sizeof(sendBuffer));
        struct timeval timeout = {0, 300000};
        setsockopt(client, SOL_SOCKET, SO_SNDTIMEO, &timeout, sizeof(timeout));

        if (!XLSendGreeting(client)) {
            close(client);
            return;
        }

        XLConnectionContext context;
        context.socketHandle = client;
        context.connected.store(true);
        context.sequence.store(1);

        XLFrameSource *frameSource = [[XLFrameSource alloc] initWithOutputWidth:XLVideoWidth
                                                                        height:XLVideoHeight];
        VTCompressionSessionRef encoder = frameSource ? XLCreateEncoder(&context) : NULL;
        if (!frameSource || !encoder) {
            close(client);
            return;
        }

        int64_t frameIndex = 0;
        const useconds_t frameInterval = (useconds_t)(1000000 / XLVideoFPS);
        while (context.connected.load()) {
            @autoreleasepool {
                uint64_t started = mach_absolute_time();
                CVPixelBufferRef pixelBuffer = NULL;
                NSInteger slotIndex = -1;
                if ([frameSource captureLatestFrame:&pixelBuffer slotIndex:&slotIndex]) {
                    XLFrameToken *token = new XLFrameToken();
                    token->frameSource = (__bridge void *)frameSource;
                    token->slotIndex = slotIndex;

                    NSDictionary *options = nil;
                    if ((frameIndex % (XLVideoFPS * 2)) == 0) {
                        options = @{(__bridge NSString *)kVTEncodeFrameOptionKey_ForceKeyFrame : @YES};
                    }
                    OSStatus encodeStatus = VTCompressionSessionEncodeFrame(
                        encoder,
                        pixelBuffer,
                        CMTimeMake(frameIndex, XLVideoFPS),
                        CMTimeMake(1, XLVideoFPS),
                        (__bridge CFDictionaryRef)options,
                        token,
                        NULL);
                    if (encodeStatus != noErr) {
                        XLReleaseFrameToken(token);
                    }
                    frameIndex++;
                }

                mach_timebase_info_data_t timebase;
                mach_timebase_info(&timebase);
                uint64_t elapsedNs = (mach_absolute_time() - started) * timebase.numer / timebase.denom;
                useconds_t elapsedUs = (useconds_t)(elapsedNs / 1000ULL);
                if (elapsedUs < frameInterval) usleep(frameInterval - elapsedUs);
            }
        }

        VTCompressionSessionCompleteFrames(encoder, kCMTimeInvalid);
        VTCompressionSessionInvalidate(encoder);
        CFRelease(encoder);
        shutdown(client, SHUT_RDWR);
        close(client);
    }
}

static void XLRunVideoServer(void) {
    int server = socket(AF_INET, SOCK_STREAM, 0);
    if (server < 0) return;
    int enabled = 1;
    setsockopt(server, SOL_SOCKET, SO_REUSEADDR, &enabled, sizeof(enabled));

    struct sockaddr_in address = {};
    address.sin_family = AF_INET;
    address.sin_addr.s_addr = htonl(INADDR_ANY);
    address.sin_port = htons(XLVideoPort);
    if (bind(server, (struct sockaddr *)&address, sizeof(address)) != 0 ||
        listen(server, 2) != 0) {
        close(server);
        return;
    }

    while (true) {
        int client = accept(server, NULL, NULL);
        if (client < 0) continue;
        // 同一台手机只运行一个编码会话；新连接在旧连接结束后进入。
        XLHandleVideoClient(client);
    }
}

void XLStartVideoServer(void) {
    static dispatch_once_t onceToken;
    dispatch_once(&onceToken, ^{
        dispatch_async(dispatch_get_global_queue(QOS_CLASS_USER_INTERACTIVE, 0), ^{
            XLRunVideoServer();
        });
    });
}

