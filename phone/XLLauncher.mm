#import <Foundation/Foundation.h>

#include <dlfcn.h>
#include <errno.h>
#include <fcntl.h>
#include <signal.h>
#include <spawn.h>
#include <arpa/inet.h>
#include <netinet/in.h>
#include <sys/socket.h>
#include <sys/file.h>
#include <sys/time.h>
#include <sys/types.h>
#include <sys/wait.h>
#include <unistd.h>

extern char **environ;

namespace {
volatile sig_atomic_t gStopRequested = 0;
volatile sig_atomic_t gChildPid = -1;
int gLauncherLock = -1;

bool XLAcquireLauncherLock(void) {
    const char *lockPath = "/var/mobile/Media/.xlstream-launcher.lock";
    gLauncherLock = open(lockPath, O_CREAT | O_RDWR, 0644);
    if (gLauncherLock < 0) {
        NSLog(@"[XLStreamLauncher] lock open failed: %d", errno);
        return false;
    }
    if (flock(gLauncherLock, LOCK_EX | LOCK_NB) != 0) {
        NSLog(@"[XLStreamLauncher] another launcher already owns the service");
        close(gLauncherLock);
        gLauncherLock = -1;
        return false;
    }
    ftruncate(gLauncherLock, 0);
    dprintf(gLauncherLock, "%d\n", getpid());
    return true;
}

bool XLPortIsListening(uint16_t port) {
    int socketDescriptor = socket(AF_INET, SOCK_STREAM, 0);
    if (socketDescriptor < 0) {
        return false;
    }

    struct timeval timeout = {0, 150000};
    setsockopt(socketDescriptor, SOL_SOCKET, SO_SNDTIMEO, &timeout, sizeof(timeout));
    setsockopt(socketDescriptor, SOL_SOCKET, SO_RCVTIMEO, &timeout, sizeof(timeout));

    struct sockaddr_in address = {};
    address.sin_family = AF_INET;
    address.sin_port = htons(port);
    address.sin_addr.s_addr = htonl(INADDR_LOOPBACK);
    bool listening = connect(socketDescriptor,
                             reinterpret_cast<struct sockaddr *>(&address),
                             sizeof(address)) == 0;
    close(socketDescriptor);
    return listening;
}

bool XLServiceIsAlreadyRunning(void) {
    return XLPortIsListening(6202) || XLPortIsListening(6203) || XLPortIsListening(6204);
}

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

}  // namespace

int main(int argc, char *argv[]) {
    (void)argc;
    @autoreleasepool {
        signal(SIGTERM, XLHandleSignal);
        signal(SIGINT, XLHandleSignal);
        signal(SIGHUP, XLHandleSignal);

        if (!XLAcquireLauncherLock()) {
            return 0;
        }

        // During an in-place Sileo update the existing root daemon normally
        // stays alive.  A SpringBoard restart must not start a second copy.
        if (XLServiceIsAlreadyRunning()) {
            NSLog(@"[XLStreamLauncher] service is already healthy");
            return 0;
        }

        NSString *servicePath = XLServicePath(argv[0]);
        NSLog(@"[XLStreamLauncher] launcher=%@ service=%@ uid=%d euid=%d",
              [NSString stringWithUTF8String:argv[0] ?: ""],
              servicePath,
              getuid(),
              geteuid());
        if (![[NSFileManager defaultManager] isExecutableFileAtPath:servicePath]) {
            NSLog(@"[XLStreamLauncher] service is missing: %@", servicePath);
            return 2;
        }

        const char *service = servicePath.fileSystemRepresentation;
        while (!gStopRequested) {
            posix_spawnattr_t attributes;
            int attrResult = posix_spawnattr_init(&attributes);
            if (attrResult != 0) {
                NSLog(@"[XLStreamLauncher] spawn attributes init failed: %d", attrResult);
                sleep(2);
                continue;
            }

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
                if (WIFEXITED(status) && WEXITSTATUS(status) == 73) {
                    NSLog(@"[XLStreamLauncher] service lock is already owned");
                    return 0;
                }
                NSLog(@"[XLStreamLauncher] service exited: %d", status);
                sleep(2);
            }
        }
    }
    return 0;
}
