#import <Foundation/Foundation.h>

#include <dlfcn.h>
#include <errno.h>
#include <signal.h>
#include <spawn.h>
#include <sys/types.h>
#include <sys/wait.h>
#include <unistd.h>

extern char **environ;

namespace {
volatile sig_atomic_t gStopRequested = 0;
volatile sig_atomic_t gChildPid = -1;

void XLHandleSignal(int signalNumber) {
    (void)signalNumber;
    gStopRequested = 1;
    pid_t child = (pid_t)gChildPid;
    if (child > 0) {
        kill(child, SIGTERM);
    }
}

NSString *XLServicePath(const char *launcherArgument) {
    NSString *bundlePath = NSBundle.mainBundle.bundlePath;
    if (![bundlePath.pathExtension.lowercaseString isEqualToString:@"app"]) {
        NSString *launcherPath = [NSString stringWithUTF8String:launcherArgument ?: ""];
        bundlePath = launcherPath.stringByDeletingLastPathComponent;
    }
    return [[bundlePath stringByAppendingPathComponent:@"bin"]
            stringByAppendingPathComponent:@"xlstreamd"];
}

void XLConfigureRootPersona(posix_spawnattr_t *attributes) {
    using SetPersona = int (*)(const posix_spawnattr_t *, uid_t, uint32_t);
    using SetPersonaId = int (*)(const posix_spawnattr_t *, uid_t);

    auto setPersona = reinterpret_cast<SetPersona>(
        dlsym(RTLD_DEFAULT, "posix_spawnattr_set_persona_np"));
    auto setPersonaUid = reinterpret_cast<SetPersonaId>(
        dlsym(RTLD_DEFAULT, "posix_spawnattr_set_persona_uid_np"));
    auto setPersonaGid = reinterpret_cast<SetPersonaId>(
        dlsym(RTLD_DEFAULT, "posix_spawnattr_set_persona_gid_np"));

    if (setPersona && setPersonaUid && setPersonaGid) {
        setPersona(attributes, 99, 1);
        setPersonaUid(attributes, 0);
        setPersonaGid(attributes, 0);
    }
}
}  // namespace

int main(int argc, char *argv[]) {
    (void)argc;
    @autoreleasepool {
        signal(SIGTERM, XLHandleSignal);
        signal(SIGINT, XLHandleSignal);
        signal(SIGHUP, XLHandleSignal);

        NSString *servicePath = XLServicePath(argv[0]);
        if (![[NSFileManager defaultManager] isExecutableFileAtPath:servicePath]) {
            NSLog(@"[XLStreamLauncher] service is missing: %@", servicePath);
            return 2;
        }

        const char *service = servicePath.fileSystemRepresentation;
        while (!gStopRequested) {
            posix_spawnattr_t attributes;
            posix_spawnattr_init(&attributes);
            XLConfigureRootPersona(&attributes);

            pid_t child = -1;
            char *const childArguments[] = {
                const_cast<char *>(service),
                nullptr,
            };
            int result = posix_spawn(&child,
                                     service,
                                     nullptr,
                                     &attributes,
                                     childArguments,
                                     environ);
            posix_spawnattr_destroy(&attributes);

            if (result != 0) {
                NSLog(@"[XLStreamLauncher] spawn failed: %d", result);
                sleep(2);
                continue;
            }

            gChildPid = child;
            NSLog(@"[XLStreamLauncher] service started: %d", child);
            int status = 0;
            while (waitpid(child, &status, 0) < 0 && errno == EINTR && !gStopRequested) {
            }
            gChildPid = -1;

            if (!gStopRequested) {
                NSLog(@"[XLStreamLauncher] service exited: %d", status);
                sleep(2);
            }
        }
    }
    return 0;
}
