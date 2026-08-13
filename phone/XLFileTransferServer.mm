#import "XLFileTransferServer.h"

#import "XLControlProtocol.h"

#import <Foundation/Foundation.h>
#import <UIKit/UIKit.h>

#include <arpa/inet.h>
#include <grp.h>
#include <netinet/in.h>
#include <pwd.h>
#include <sys/stat.h>
#include <sys/socket.h>
#include <unistd.h>

static const uint64_t XLMaximumFileSize = 8ULL * 1024ULL * 1024ULL * 1024ULL;
static const uint32_t XLMaximumFolderMetadataSize = 16U * 1024U * 1024U;
static const NSUInteger XLMaximumFolderEntries = 100000;
static NSString *const XLLocalFileProviderGroupIdentifier =
    @"group.com.apple.FileProvider.LocalStorage";

static BOOL XLFileReadAll(int socketHandle, void *buffer, size_t length) {
    uint8_t *bytes = (uint8_t *)buffer;
    size_t offset = 0;
    while (offset < length) {
        ssize_t received = recv(socketHandle, bytes + offset, length - offset, 0);
        if (received <= 0) return NO;
        offset += (size_t)received;
    }
    return YES;
}

static BOOL XLFileWriteAll(int socketHandle, const void *buffer, size_t length) {
    const uint8_t *bytes = (const uint8_t *)buffer;
    size_t offset = 0;
    while (offset < length) {
        ssize_t written = send(socketHandle, bytes + offset, length - offset, MSG_NOSIGNAL);
        if (written <= 0) return NO;
        offset += (size_t)written;
    }
    return YES;
}

static void XLSendFileResult(int client, BOOL success, NSString *message, NSString *path) {
    NSDictionary *result = @{
        @"success" : @(success),
        @"message" : message ?: @"",
        @"path" : path ?: @"",
    };
    NSData *json = [NSJSONSerialization dataWithJSONObject:result options:0 error:nil];
    if (!json.length) return;
    NSMutableData *line = [json mutableCopy];
    [line appendBytes:"\n" length:1];
    XLFileWriteAll(client, line.bytes, line.length);
}

static NSString *XLSafeFileName(NSString *rawName) {
    NSString *name = rawName.lastPathComponent;
    if (!name.length || [name isEqualToString:@"."] || [name isEqualToString:@".."]) {
        name = [NSString stringWithFormat:@"file-%@.bin", NSUUID.UUID.UUIDString];
    }
    NSCharacterSet *forbidden = [NSCharacterSet characterSetWithCharactersInString:@"/\\:"];
    name = [[name componentsSeparatedByCharactersInSet:forbidden] componentsJoinedByString:@"_"];
    return name.length ? name : @"received-file.bin";
}

static NSString *XLUniqueDestination(NSString *directory, NSString *fileName) {
    NSFileManager *manager = NSFileManager.defaultManager;
    NSString *destination = [directory stringByAppendingPathComponent:fileName];
    if (![manager fileExistsAtPath:destination]) return destination;

    NSString *base = fileName.stringByDeletingPathExtension;
    NSString *extension = fileName.pathExtension;
    for (NSUInteger index = 1; index < 10000; index++) {
        NSString *candidateName = extension.length
            ? [NSString stringWithFormat:@"%@-%lu.%@", base, (unsigned long)index, extension]
            : [NSString stringWithFormat:@"%@-%lu", base, (unsigned long)index];
        NSString *candidate = [directory stringByAppendingPathComponent:candidateName];
        if (![manager fileExistsAtPath:candidate]) return candidate;
    }
    return [directory stringByAppendingPathComponent:
            [NSString stringWithFormat:@"%@-%@", NSUUID.UUID.UUIDString, fileName]];
}

static NSString *XLTransferDocumentsDirectory(void) {
    NSFileManager *manager = NSFileManager.defaultManager;
    NSArray<NSString *> *sharedContainerRoots = @[
        @"/var/mobile/Containers/Shared/AppGroup",
        @"/private/var/mobile/Containers/Shared/AppGroup",
    ];
    for (NSString *root in sharedContainerRoots) {
        NSArray<NSString *> *entries = [manager contentsOfDirectoryAtPath:root error:nil];
        for (NSString *entry in entries) {
            NSString *container = [root stringByAppendingPathComponent:entry];
            NSString *metadataPath = [container stringByAppendingPathComponent:
                @".com.apple.mobile_container_manager.metadata.plist"];
            NSDictionary *metadata = [NSDictionary dictionaryWithContentsOfFile:metadataPath];
            if ([metadata[@"MCMMetadataIdentifier"]
                    isEqualToString:XLLocalFileProviderGroupIdentifier]) {
                NSString *storage = [container stringByAppendingPathComponent:@"File Provider Storage"];
                return [storage stringByAppendingPathComponent:@"星澜传输"];
            }
        }
    }

    // This path is always writable by the root daemon and remains available
    // even when the local Files provider container cannot be discovered.
    return @"/var/mobile/Media/Downloads/星澜传输";
}

static void XLApplyMobilePermissions(NSString *path, mode_t mode) {
    if (!path.length) return;
    struct passwd *mobileAccount = getpwnam("mobile");
    uid_t owner = mobileAccount ? mobileAccount->pw_uid : 501;
    gid_t group = mobileAccount ? mobileAccount->pw_gid : 501;
    struct group *mobileGroup = getgrnam("mobile");
    if (mobileGroup) group = mobileGroup->gr_gid;
    const char *fileSystemPath = path.fileSystemRepresentation;
    if (!fileSystemPath) return;
    chown(fileSystemPath, owner, group);
    chmod(fileSystemPath, mode);
}

static void XLRepairTransferDirectoryPermissions(NSString *directory) {
    if (!directory.length) return;
    XLApplyMobilePermissions(directory, 0755);
    NSFileManager *manager = NSFileManager.defaultManager;
    NSDirectoryEnumerator<NSString *> *entries = [manager enumeratorAtPath:directory];
    for (NSString *entry in entries) {
        NSString *path = [directory stringByAppendingPathComponent:entry];
        BOOL isDirectory = NO;
        if ([manager fileExistsAtPath:path isDirectory:&isDirectory]) {
            XLApplyMobilePermissions(path, isDirectory ? 0755 : 0644);
        }
    }
}

static NSString *XLSafeRelativePath(id rawValue) {
    if (![rawValue isKindOfClass:NSString.class]) return nil;
    NSString *rawPath = [(NSString *)rawValue stringByReplacingOccurrencesOfString:@"\\"
                                                                        withString:@"/"];
    if (!rawPath.length || [rawPath hasPrefix:@"/"] ||
        [rawPath rangeOfCharacterFromSet:NSCharacterSet.controlCharacterSet].location != NSNotFound) {
        return nil;
    }
    NSArray<NSString *> *components = [rawPath componentsSeparatedByString:@"/"];
    NSMutableArray<NSString *> *safeComponents = [NSMutableArray arrayWithCapacity:components.count];
    NSCharacterSet *forbidden = [NSCharacterSet characterSetWithCharactersInString:@":"];
    for (NSString *component in components) {
        if (!component.length || [component isEqualToString:@"."] ||
            [component isEqualToString:@".."] ||
            [component rangeOfCharacterFromSet:forbidden].location != NSNotFound) {
            return nil;
        }
        [safeComponents addObject:component];
    }
    return [safeComponents componentsJoinedByString:@"/"];
}

static BOOL XLReceiveFilePayload(int client, NSString *destination, uint64_t fileLength) {
    NSFileManager *manager = NSFileManager.defaultManager;
    NSString *parent = destination.stringByDeletingLastPathComponent;
    if (![manager createDirectoryAtPath:parent
             withIntermediateDirectories:YES
                              attributes:nil
                                   error:nil]) {
        return NO;
    }
    if (![manager createFileAtPath:destination contents:nil attributes:nil]) return NO;

    NSFileHandle *output = [NSFileHandle fileHandleForWritingAtPath:destination];
    if (!output) {
        [manager removeItemAtPath:destination error:nil];
        return NO;
    }

    BOOL receivedSuccessfully = YES;
    uint64_t remaining = fileLength;
    uint8_t buffer[256 * 1024];
    @try {
        while (remaining > 0) {
            size_t wanted = (size_t)MIN((uint64_t)sizeof(buffer), remaining);
            ssize_t received = recv(client, buffer, wanted, 0);
            if (received <= 0) {
                receivedSuccessfully = NO;
                break;
            }
            [output writeData:[NSData dataWithBytesNoCopy:buffer
                                                   length:(NSUInteger)received
                                             freeWhenDone:NO]];
            remaining -= (uint64_t)received;
        }
        [output synchronizeFile];
        [output closeFile];
    } @catch (__unused NSException *exception) {
        receivedSuccessfully = NO;
        @try {
            [output closeFile];
        } @catch (__unused NSException *ignored) {
        }
    }

    if (!receivedSuccessfully || remaining != 0) {
        [manager removeItemAtPath:destination error:nil];
        return NO;
    }
    XLApplyMobilePermissions(destination, 0644);
    return YES;
}

static BOOL XLReceiveFolderPayload(int client,
                                   NSDictionary *metadata,
                                   uint64_t payloadLength,
                                   NSString *transferDirectory,
                                   NSString **savedPath,
                                   NSString **failureMessage) {
    NSArray *entries = metadata[@"entries"];
    if (![entries isKindOfClass:NSArray.class] || entries.count > XLMaximumFolderEntries) {
        if (failureMessage) *failureMessage = @"文件夹目录清单无效";
        return NO;
    }

    NSMutableArray<NSDictionary *> *validated = [NSMutableArray arrayWithCapacity:entries.count];
    uint64_t expectedPayloadLength = 0;
    for (id rawEntry in entries) {
        if (![rawEntry isKindOfClass:NSDictionary.class]) {
            if (failureMessage) *failureMessage = @"文件夹目录项无法识别";
            return NO;
        }
        NSDictionary *entry = (NSDictionary *)rawEntry;
        NSString *relativePath = XLSafeRelativePath(entry[@"path"]);
        if (!relativePath.length) {
            if (failureMessage) *failureMessage = @"文件夹包含不安全路径";
            return NO;
        }
        BOOL isDirectory = [entry[@"directory"] boolValue];
        uint64_t size = 0;
        if (!isDirectory) {
            id rawSize = entry[@"size"];
            if (![rawSize isKindOfClass:NSNumber.class] || [rawSize longLongValue] < 0) {
                if (failureMessage) *failureMessage = @"文件夹中的文件大小无效";
                return NO;
            }
            size = [rawSize unsignedLongLongValue];
            if (size > XLMaximumFileSize ||
                expectedPayloadLength > XLMaximumFileSize - size) {
                if (failureMessage) *failureMessage = @"文件夹内容合计超过 8GB";
                return NO;
            }
            expectedPayloadLength += size;
        }
        [validated addObject:@{
            @"path" : relativePath,
            @"directory" : @(isDirectory),
            @"size" : @(size),
        }];
    }
    if (expectedPayloadLength != payloadLength) {
        if (failureMessage) *failureMessage = @"文件夹数据长度与目录清单不一致";
        return NO;
    }

    NSString *folderName = XLSafeFileName([metadata[@"name"] description]);
    if (!folderName.length) folderName = @"received-folder";
    NSString *rootDestination = XLUniqueDestination(transferDirectory, folderName);
    NSFileManager *manager = NSFileManager.defaultManager;
    if (![manager createDirectoryAtPath:rootDestination
             withIntermediateDirectories:YES
                              attributes:nil
                                   error:nil]) {
        if (failureMessage) *failureMessage = @"无法创建手机端文件夹";
        return NO;
    }

    for (NSDictionary *entry in validated) {
        NSString *destination = [rootDestination stringByAppendingPathComponent:entry[@"path"]];
        if ([entry[@"directory"] boolValue]) {
            if (![manager createDirectoryAtPath:destination
                     withIntermediateDirectories:YES
                                      attributes:nil
                                           error:nil]) {
                [manager removeItemAtPath:rootDestination error:nil];
                if (failureMessage) *failureMessage = @"无法创建手机端子目录";
                return NO;
            }
            continue;
        }
        if (!XLReceiveFilePayload(client, destination, [entry[@"size"] unsignedLongLongValue])) {
            [manager removeItemAtPath:rootDestination error:nil];
            if (failureMessage) *failureMessage = @"USB传输中断，未保留残缺文件夹";
            return NO;
        }
    }

    XLRepairTransferDirectoryPermissions(rootDestination);
    if (savedPath) *savedPath = rootDestination;
    return YES;
}

static void XLImportMediaAtPath(NSString *path) {
    NSString *extension = path.pathExtension.lowercaseString;
    NSSet<NSString *> *videoExtensions = [NSSet setWithArray:@[
        @"mp4", @"mov", @"m4v", @"3gp"
    ]];
    if ([videoExtensions containsObject:extension]) {
        if (!UIVideoAtPathIsCompatibleWithSavedPhotosAlbum(path)) return;
        dispatch_async(dispatch_get_main_queue(), ^{
            UISaveVideoAtPathToSavedPhotosAlbum(path, nil, nil, nil);
        });
        return;
    }

    NSData *data = [NSData dataWithContentsOfFile:path
                                         options:NSDataReadingMappedIfSafe
                                           error:nil];
    UIImage *image = data ? [UIImage imageWithData:data] : nil;
    if (!image) return;
    dispatch_async(dispatch_get_main_queue(), ^{
        UIImageWriteToSavedPhotosAlbum(image, nil, nil, nil);
    });
}

static void XLHandleFileClient(int client) {
    @autoreleasepool {
        int noSignal = 1;
        setsockopt(client, SOL_SOCKET, SO_NOSIGPIPE, &noSignal, sizeof(noSignal));

        do {
            uint8_t header[16] = {0};
            if (!XLFileReadAll(client, header, sizeof(header))) {
                XLSendFileResult(client, NO, @"文件协议错误", nil);
                break;
            }
            BOOL isSingleFile = memcmp(header, "XLFT", 4) == 0;
            BOOL isFolder = memcmp(header, "XLFD", 4) == 0;
            if (!isSingleFile && !isFolder) {
                XLSendFileResult(client, NO, @"文件协议错误", nil);
                break;
            }

            uint32_t metadataLengthBE = 0;
            uint64_t fileLengthBE = 0;
            memcpy(&metadataLengthBE, header + 4, sizeof(metadataLengthBE));
            memcpy(&fileLengthBE, header + 8, sizeof(fileLengthBE));
            uint32_t metadataLength = ntohl(metadataLengthBE);
            uint64_t fileLength = CFSwapInt64BigToHost(fileLengthBE);
            uint32_t maximumMetadataLength = isFolder
                ? XLMaximumFolderMetadataSize
                : 64U * 1024U;
            if (metadataLength == 0 || metadataLength > maximumMetadataLength ||
                (isSingleFile && fileLength == 0) || fileLength > XLMaximumFileSize) {
                XLSendFileResult(client, NO, @"文件大小或信息无效", nil);
                break;
            }

            NSMutableData *metadataData = [NSMutableData dataWithLength:metadataLength];
            if (!XLFileReadAll(client, metadataData.mutableBytes, metadataLength)) {
                XLSendFileResult(client, NO, @"文件信息接收失败", nil);
                break;
            }
            NSDictionary *metadata =
                [NSJSONSerialization JSONObjectWithData:metadataData options:0 error:nil];
            if (![metadata isKindOfClass:NSDictionary.class]) {
                XLSendFileResult(client, NO, @"文件信息无法识别", nil);
                break;
            }

            NSString *directory = XLTransferDocumentsDirectory();
            NSError *directoryError = nil;
            if (![NSFileManager.defaultManager createDirectoryAtPath:directory
                                          withIntermediateDirectories:YES
                                                           attributes:nil
                                                                error:&directoryError]) {
                XLSendFileResult(client, NO,
                    directoryError.localizedDescription ?: @"无法创建保存目录", nil);
                break;
            }
            XLRepairTransferDirectoryPermissions(directory);

            if (isFolder) {
                NSString *savedPath = nil;
                NSString *failureMessage = nil;
                if (!XLReceiveFolderPayload(client, metadata, fileLength, directory,
                                            &savedPath, &failureMessage)) {
                    XLSendFileResult(client, NO,
                        failureMessage ?: @"文件夹传输失败", nil);
                    break;
                }
                XLSendFileResult(client, YES, @"文件夹已保存", savedPath);
                break;
            }

            NSString *fileName = XLSafeFileName([metadata[@"name"] description]);
            BOOL importPhoto = [metadata[@"importPhoto"] boolValue];
            NSString *destination = XLUniqueDestination(directory, fileName);
            if (!XLReceiveFilePayload(client, destination, fileLength)) {
                XLSendFileResult(client, NO, @"USB传输中断，未保留残缺文件", nil);
                break;
            }

            if (importPhoto) XLImportMediaAtPath(destination);
            NSString *message = importPhoto
                ? @"文件已保存，并已提交到系统相册"
                : @"文件已保存";
            XLSendFileResult(client, YES, message, destination);
        } while (false);

        shutdown(client, SHUT_RDWR);
        close(client);
    }
}

static void XLRunFileTransferServer(void) {
    NSString *directory = XLTransferDocumentsDirectory();
    [NSFileManager.defaultManager createDirectoryAtPath:directory
                            withIntermediateDirectories:YES
                                             attributes:nil
                                                  error:nil];
    XLRepairTransferDirectoryPermissions(directory);

    int server = socket(AF_INET, SOCK_STREAM, 0);
    if (server < 0) return;
    int enabled = 1;
    setsockopt(server, SOL_SOCKET, SO_REUSEADDR, &enabled, sizeof(enabled));
    struct sockaddr_in address = {};
    address.sin_family = AF_INET;
    address.sin_addr.s_addr = htonl(INADDR_ANY);
    address.sin_port = htons(XLFileTransferPort);
    if (bind(server, (struct sockaddr *)&address, sizeof(address)) != 0 ||
        listen(server, 16) != 0) {
        close(server);
        return;
    }

    while (true) {
        int client = accept(server, NULL, NULL);
        if (client < 0) continue;
        dispatch_async(dispatch_get_global_queue(QOS_CLASS_UTILITY, 0), ^{
            XLHandleFileClient(client);
        });
    }
}

void XLStartFileTransferServer(void) {
    static dispatch_once_t onceToken;
    dispatch_once(&onceToken, ^{
        dispatch_async(dispatch_get_global_queue(QOS_CLASS_UTILITY, 0), ^{
            while (true) {
                XLRunFileTransferServer();
                sleep(2);
            }
        });
    });
}
