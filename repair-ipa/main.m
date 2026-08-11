#import <UIKit/UIKit.h>
#import <Foundation/Foundation.h>
#import <sys/stat.h>
#import <unistd.h>

@interface RepairViewController : UIViewController
@end

@interface RepairAppDelegate : UIResponder <UIApplicationDelegate>
@property(nonatomic, strong) UIWindow *window;
@end

static BOOL XLWriteSafeScript(NSString *path, NSMutableString *report)
{
    NSFileManager *fm = NSFileManager.defaultManager;
    if (![fm fileExistsAtPath:path]) {
        return NO;
    }

    NSString *backup = [path stringByAppendingString:@".xlstream-repair.bak"];
    if (![fm fileExistsAtPath:backup]) {
        NSError *backupError = nil;
        if (![fm copyItemAtPath:path toPath:backup error:&backupError]) {
            [report appendFormat:@"BACKUP_FAILED %@: %@\n", path, backupError.localizedDescription];
            return NO;
        }
        [report appendFormat:@"BACKUP %@\n", backup];
    }

    NSData *safeScript = [@"#!/bin/sh\nexit 0\n" dataUsingEncoding:NSUTF8StringEncoding];
    NSError *writeError = nil;
    if (![safeScript writeToFile:path options:NSDataWritingAtomic error:&writeError]) {
        [report appendFormat:@"WRITE_FAILED %@: %@\n", path, writeError.localizedDescription];
        return NO;
    }
    chmod(path.fileSystemRepresentation, 0755);
    chown(path.fileSystemRepresentation, 0, 0);

    NSData *verify = [NSData dataWithContentsOfFile:path];
    if (![verify isEqualToData:safeScript]) {
        [report appendFormat:@"VERIFY_FAILED %@\n", path];
        return NO;
    }
    [report appendFormat:@"REPAIRED %@\n", path];
    return YES;
}

static int XLRepairMain(void)
{
    @autoreleasepool {
        NSFileManager *fm = NSFileManager.defaultManager;
        NSMutableString *report = [NSMutableString string];
        [report appendFormat:@"CREDENTIAL uid=%u euid=%u gid=%u egid=%u\n",
         (unsigned)getuid(), (unsigned)geteuid(), (unsigned)getgid(), (unsigned)getegid()];
        if (geteuid() != 0) {
            [report appendString:@"RESULT_NOT_ROOT\n"];
            fputs(report.UTF8String, stdout);
            fflush(stdout);
            return 21;
        }

        NSMutableOrderedSet<NSString *> *roots = [NSMutableOrderedSet orderedSet];
        NSString *containerRoot = @"/private/var/containers/Bundle/Application";
        NSError *listError = nil;
        NSArray<NSString *> *entries = [fm contentsOfDirectoryAtPath:containerRoot error:&listError];
        for (NSString *entry in entries ?: @[]) {
            if ([entry hasPrefix:@".jbroot-"]) {
                [roots addObject:[containerRoot stringByAppendingPathComponent:entry]];
            }
        }
        [roots addObject:@"/"];
        [roots addObject:@"/var/jb"];

        if (listError) {
            [report appendFormat:@"SCAN_WARNING %@\n", listError.localizedDescription];
        }

        NSUInteger repaired = 0;
        for (NSString *root in roots) {
            NSString *infoDir = [root stringByAppendingPathComponent:@"var/lib/dpkg/info"];
            NSString *statusPath = [root stringByAppendingPathComponent:@"var/lib/dpkg/status"];
            if (![fm fileExistsAtPath:statusPath]) {
                continue;
            }

            NSString *prerm = [infoDir stringByAppendingPathComponent:@"com.jibeib.xlstream.prerm"];
            NSString *postinst = [infoDir stringByAppendingPathComponent:@"com.jibeib.xlstream.postinst"];
            BOOL found = [fm fileExistsAtPath:prerm] || [fm fileExistsAtPath:postinst];
            if (!found) {
                continue;
            }

            [report appendFormat:@"FOUND %@\n", root];
            repaired += XLWriteSafeScript(prerm, report) ? 1 : 0;
            repaired += XLWriteSafeScript(postinst, report) ? 1 : 0;
        }

        if (repaired == 0) {
            [report appendString:@"RESULT_NOT_FOUND\n"];
        } else {
            [report appendFormat:@"RESULT_OK %lu\n", (unsigned long)repaired];
        }
        fputs(report.UTF8String, stdout);
        fflush(stdout);
        return repaired > 0 ? 0 : 20;
    }
}

int main(int argc, char *argv[])
{
    if (argc > 1 && strcmp(argv[1], "--repair-helper") == 0) {
        return XLRepairMain();
    }
    @autoreleasepool {
        return UIApplicationMain(argc, argv, nil, NSStringFromClass(RepairAppDelegate.class));
    }
}

@implementation RepairAppDelegate
- (BOOL)application:(UIApplication *)application didFinishLaunchingWithOptions:(NSDictionary *)launchOptions
{
    (void)application;
    (void)launchOptions;
    self.window = [[UIWindow alloc] initWithFrame:UIScreen.mainScreen.bounds];
    self.window.rootViewController = [RepairViewController new];
    [self.window makeKeyAndVisible];
    return YES;
}
@end
