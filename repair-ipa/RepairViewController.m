#import <UIKit/UIKit.h>
#import <Foundation/Foundation.h>
#import <spawn.h>
#import <sys/wait.h>
#import <unistd.h>

// These private spawn declarations are intentionally the same interface used
// by TrollStore's official root helper implementation.
#define POSIX_SPAWN_PERSONA_FLAGS_OVERRIDE 1
extern int posix_spawnattr_set_persona_np(const posix_spawnattr_t * __restrict, uid_t, uint32_t);
extern int posix_spawnattr_set_persona_uid_np(const posix_spawnattr_t * __restrict, uid_t);
extern int posix_spawnattr_set_persona_gid_np(const posix_spawnattr_t * __restrict, uid_t);

@interface RepairViewController : UIViewController
@property(nonatomic, strong) UILabel *stateLabel;
@property(nonatomic, strong) UITextView *logView;
@property(nonatomic, strong) UIButton *repairButton;
@end

static int XLSpawnRootSelf(NSString **output)
{
    NSString *path = NSBundle.mainBundle.executablePath;
    const char *args[] = {path.fileSystemRepresentation, "--repair-helper", NULL};

    int pipeFD[2];
    if (pipe(pipeFD) != 0) {
        return 30;
    }

    posix_spawnattr_t attr;
    posix_spawnattr_init(&attr);
    posix_spawnattr_set_persona_np(&attr, 99, POSIX_SPAWN_PERSONA_FLAGS_OVERRIDE);
    posix_spawnattr_set_persona_uid_np(&attr, 0);
    posix_spawnattr_set_persona_gid_np(&attr, 0);

    posix_spawn_file_actions_t actions;
    posix_spawn_file_actions_init(&actions);
    posix_spawn_file_actions_adddup2(&actions, pipeFD[1], STDOUT_FILENO);
    posix_spawn_file_actions_adddup2(&actions, pipeFD[1], STDERR_FILENO);
    posix_spawn_file_actions_addclose(&actions, pipeFD[0]);
    posix_spawn_file_actions_addclose(&actions, pipeFD[1]);

    pid_t pid = 0;
    int spawnError = posix_spawn(&pid, path.fileSystemRepresentation, &actions, &attr, (char *const *)args, NULL);
    posix_spawn_file_actions_destroy(&actions);
    posix_spawnattr_destroy(&attr);
    close(pipeFD[1]);
    if (spawnError != 0) {
        close(pipeFD[0]);
        return spawnError;
    }

    NSMutableData *data = [NSMutableData data];
    uint8_t buffer[1024];
    ssize_t count = 0;
    while ((count = read(pipeFD[0], buffer, sizeof(buffer))) > 0) {
        [data appendBytes:buffer length:(NSUInteger)count];
    }
    close(pipeFD[0]);

    int status = 0;
    waitpid(pid, &status, 0);
    if (output) {
        *output = [[NSString alloc] initWithData:data encoding:NSUTF8StringEncoding] ?: @"";
    }
    if (!WIFEXITED(status)) {
        return 31;
    }
    return WEXITSTATUS(status);
}

@implementation RepairViewController

- (void)viewDidLoad
{
    [super viewDidLoad];
    self.view.backgroundColor = [UIColor colorWithRed:0.04 green:0.08 blue:0.14 alpha:1.0];

    UILabel *title = [UILabel new];
    title.translatesAutoresizingMaskIntoConstraints = NO;
    title.text = @"XLStream RootHide 修复";
    title.textColor = UIColor.whiteColor;
    title.font = [UIFont boldSystemFontOfSize:25];
    title.textAlignment = NSTextAlignmentCenter;
    [self.view addSubview:title];

    self.stateLabel = [UILabel new];
    self.stateLabel.translatesAutoresizingMaskIntoConstraints = NO;
    self.stateLabel.text = @"只修复损坏的 dpkg 安装脚本\n不删除其他插件或越狱环境";
    self.stateLabel.textColor = [UIColor colorWithWhite:0.85 alpha:1.0];
    self.stateLabel.font = [UIFont systemFontOfSize:16];
    self.stateLabel.numberOfLines = 0;
    self.stateLabel.textAlignment = NSTextAlignmentCenter;
    [self.view addSubview:self.stateLabel];

    self.logView = [UITextView new];
    self.logView.translatesAutoresizingMaskIntoConstraints = NO;
    self.logView.editable = NO;
    self.logView.backgroundColor = [UIColor colorWithWhite:0.0 alpha:0.35];
    self.logView.textColor = [UIColor colorWithWhite:0.82 alpha:1.0];
    self.logView.font = [UIFont monospacedSystemFontOfSize:11 weight:UIFontWeightRegular];
    self.logView.layer.cornerRadius = 10;
    self.logView.text = @"点击下方按钮后，工具会先备份再修复。";
    [self.view addSubview:self.logView];

    self.repairButton = [UIButton buttonWithType:UIButtonTypeSystem];
    self.repairButton.translatesAutoresizingMaskIntoConstraints = NO;
    self.repairButton.backgroundColor = [UIColor colorWithRed:0.08 green:0.72 blue:0.43 alpha:1.0];
    [self.repairButton setTitle:@"开始安全修复" forState:UIControlStateNormal];
    [self.repairButton setTitleColor:UIColor.whiteColor forState:UIControlStateNormal];
    self.repairButton.titleLabel.font = [UIFont boldSystemFontOfSize:18];
    self.repairButton.layer.cornerRadius = 10;
    [self.repairButton addTarget:self action:@selector(runRepair) forControlEvents:UIControlEventTouchUpInside];
    [self.view addSubview:self.repairButton];

    UILayoutGuide *safe = self.view.safeAreaLayoutGuide;
    [NSLayoutConstraint activateConstraints:@[
        [title.topAnchor constraintEqualToAnchor:safe.topAnchor constant:30],
        [title.leadingAnchor constraintEqualToAnchor:safe.leadingAnchor constant:16],
        [title.trailingAnchor constraintEqualToAnchor:safe.trailingAnchor constant:-16],
        [self.stateLabel.topAnchor constraintEqualToAnchor:title.bottomAnchor constant:18],
        [self.stateLabel.leadingAnchor constraintEqualToAnchor:safe.leadingAnchor constant:20],
        [self.stateLabel.trailingAnchor constraintEqualToAnchor:safe.trailingAnchor constant:-20],
        [self.logView.topAnchor constraintEqualToAnchor:self.stateLabel.bottomAnchor constant:24],
        [self.logView.leadingAnchor constraintEqualToAnchor:safe.leadingAnchor constant:16],
        [self.logView.trailingAnchor constraintEqualToAnchor:safe.trailingAnchor constant:-16],
        [self.logView.bottomAnchor constraintEqualToAnchor:self.repairButton.topAnchor constant:-20],
        [self.repairButton.leadingAnchor constraintEqualToAnchor:safe.leadingAnchor constant:24],
        [self.repairButton.trailingAnchor constraintEqualToAnchor:safe.trailingAnchor constant:-24],
        [self.repairButton.bottomAnchor constraintEqualToAnchor:safe.bottomAnchor constant:-28],
        [self.repairButton.heightAnchor constraintEqualToConstant:52],
    ]];
}

- (void)runRepair
{
    self.repairButton.enabled = NO;
    self.stateLabel.text = @"正在检查并修复…";
    self.logView.text = @"正在获取 RootHide 修复权限…";

    dispatch_async(dispatch_get_global_queue(QOS_CLASS_USER_INITIATED, 0), ^{
        NSString *output = nil;
        int result = XLSpawnRootSelf(&output);
        dispatch_async(dispatch_get_main_queue(), ^{
            self.logView.text = output.length ? output : [NSString stringWithFormat:@"未收到修复日志（代码 %d）", result];
            if (result == 0 && [output containsString:@"RESULT_OK"]) {
                self.stateLabel.text = @"修复成功\n现在可安装 XLStream 0.5.5 RootHide 版";
                self.stateLabel.textColor = [UIColor colorWithRed:0.25 green:0.95 blue:0.58 alpha:1.0];
                [self.repairButton setTitle:@"已修复" forState:UIControlStateDisabled];
            } else {
                self.stateLabel.text = [NSString stringWithFormat:@"修复未完成（代码 %d）\n未找到时不会修改任何文件", result];
                self.stateLabel.textColor = [UIColor colorWithRed:1.0 green:0.42 blue:0.35 alpha:1.0];
                self.repairButton.enabled = YES;
            }
        });
    });
}

@end
