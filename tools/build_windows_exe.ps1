param(
    [Parameter(Mandatory = $true)]
    [string]$PythonExe,
    [switch]$ConsoleMain,
    [switch]$E5Optimized,
    [switch]$SmoothRenderTest,
    [switch]$ImeHostAlphaTest,
    [switch]$GroupPopupUpTest,
    [switch]$GroupButtonsTest,
    [switch]$GroupButtonsNoFlickerTest,
    [switch]$GroupButtonsNoFlickerStopFixTest,
    [switch]$GroupOneClickTest,
    [switch]$GroupTwoStepTest,
    [switch]$GroupTwoStepOtpPasteTest,
    [switch]$GroupPowerOtpTest,
    [switch]$TrollVncOtpPasteTest,
    [switch]$AutoUsbRepairTest,
    [switch]$ImeFollowTest,
    [switch]$NativeImeTest,
    [switch]$PhoneDownload01
)

# Windows PowerShell 5.1 requires a BOM to parse Chinese string literals.

$ErrorActionPreference = "Stop"
$projectRoot = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
$workspaceRoot = (Resolve-Path (Join-Path $projectRoot "..\..")).Path
$portableDeps = Join-Path $workspaceRoot "usb_capture_deps"
$usbmuxTools = Join-Path (Split-Path $projectRoot -Parent) "independent-usbmux-test"
$baselineProject = Join-Path (Split-Path $projectRoot -Parent) "星澜_USB原生群控_新版"
$configSource = Join-Path $baselineProject "config\device_groups.json"
$integratedPackage = Join-Path $projectRoot "phone\packages\星澜TP-TrollVNC静默整合版_RootHide_0.9.0.deb"
if (@($E5Optimized, $SmoothRenderTest, $ImeHostAlphaTest, $GroupPopupUpTest, $GroupButtonsTest, $GroupButtonsNoFlickerTest, $GroupButtonsNoFlickerStopFixTest, $GroupOneClickTest, $GroupTwoStepTest, $GroupTwoStepOtpPasteTest, $GroupPowerOtpTest, $TrollVncOtpPasteTest, $AutoUsbRepairTest, $ImeFollowTest, $NativeImeTest, $PhoneDownload01).Where({ $_ }).Count -gt 1) {
    throw "只能选择一种特殊构建模式"
}
$mainName = if ($PhoneDownload01) { "星澜0.1" } elseif ($NativeImeTest) { "星澜_原生输入宿主测试" } elseif ($ImeFollowTest) { "星澜_候选框跟随测试" } elseif ($AutoUsbRepairTest) { "星澜_USB掉线自动修复测试" } elseif ($TrollVncOtpPasteTest) { "星澜_TrollVNC原生验证码粘贴测试" } elseif ($GroupPowerOtpTest) { "星澜_本组开屏锁屏验证码测试" } elseif ($GroupTwoStepOtpPasteTest) { "星澜_两步投屏验证码粘贴测试" } elseif ($GroupTwoStepTest) { "星澜_分组两步投屏测试" } elseif ($GroupOneClickTest) { "星澜_分组一键连接测试" } elseif ($GroupButtonsNoFlickerStopFixTest) { "星澜_分组按钮无闪烁停止修正测试" } elseif ($GroupButtonsNoFlickerTest) { "星澜_分组按钮无闪烁测试" } elseif ($GroupButtonsTest) { "星澜_分组按钮直选测试" } elseif ($GroupPopupUpTest) { "星澜_分组向上展开测试" } elseif ($ImeHostAlphaTest) { "星澜_输入法亮点透明测试" } elseif ($SmoothRenderTest) { "星澜_EXE流畅度优化测试" } elseif ($E5Optimized) { "星澜_双路E5" } else { "星澜" }
$releaseName = if ($PhoneDownload01) { "星澜0.1" } elseif ($NativeImeTest) { "星澜_原生输入宿主测试版" } elseif ($ImeFollowTest) { "星澜_候选框跟随测试版" } elseif ($AutoUsbRepairTest) { "星澜_USB掉线自动修复测试版" } elseif ($TrollVncOtpPasteTest) { "星澜_TrollVNC原生验证码粘贴测试版" } elseif ($GroupPowerOtpTest) { "星澜_本组开屏锁屏_验证码粘贴修复测试版" } elseif ($GroupTwoStepOtpPasteTest) { "星澜_两步投屏_验证码粘贴测试版" } elseif ($GroupTwoStepTest) { "星澜_分组按钮_两步投屏测试版" } elseif ($GroupOneClickTest) { "星澜_分组按钮_一键连接测试版" } elseif ($GroupButtonsNoFlickerStopFixTest) { "星澜_分组按钮_无闪烁停止修正版" } elseif ($GroupButtonsNoFlickerTest) { "星澜_分组按钮_无闪烁测试版" } elseif ($GroupButtonsTest) { "星澜_分组按钮直选测试版" } elseif ($GroupPopupUpTest) { "星澜_分组向上展开测试版" } elseif ($ImeHostAlphaTest) { "星澜_输入法亮点透明测试版" } elseif ($SmoothRenderTest) { "星澜_EXE流畅度优化测试版" } elseif ($E5Optimized) { "星澜_双路E5优化版" } else { "星澜_TrollVNC触控版" }

function Remove-GeneratedPath {
    param(
        [Parameter(Mandatory = $true)][string]$Path,
        [Parameter(Mandatory = $true)][string]$AllowedRoot
    )
    if (-not (Test-Path -LiteralPath $Path)) { return }
    $resolvedPath = [IO.Path]::GetFullPath($Path)
    $resolvedRoot = [IO.Path]::GetFullPath($AllowedRoot).TrimEnd('\') + '\'
    if (-not $resolvedPath.StartsWith($resolvedRoot, [StringComparison]::OrdinalIgnoreCase)) {
        throw "拒绝清理项目目录以外的路径: $resolvedPath"
    }
    Remove-Item -LiteralPath $resolvedPath -Recurse -Force
}
$buildStamp = Get-Date -Format "yyyyMMdd-HHmmss"
$workRoot = Join-Path $projectRoot "build\windows-$buildStamp"
$distRoot = Join-Path $workRoot "dist"
$releaseRoot = Join-Path $projectRoot "release"

if (-not (Test-Path -LiteralPath $portableDeps -PathType Container)) {
    throw "缺少便携依赖目录: $portableDeps"
}
if (-not (Test-Path -LiteralPath $usbmuxTools -PathType Container)) {
    throw "缺少 USB 工具目录: $usbmuxTools"
}
if (-not (Test-Path -LiteralPath $configSource -PathType Leaf)) {
    throw "缺少正式版手机分组配置: $configSource"
}
if (-not (Test-Path -LiteralPath $integratedPackage -PathType Leaf)) {
    throw "缺少 RootHide 手机端整合插件: $integratedPackage"
}

New-Item -ItemType Directory -Path $workRoot -Force | Out-Null
New-Item -ItemType Directory -Path $releaseRoot -Force | Out-Null

$commonArgs = @(
    "--noconfirm", "--clean",
    "--paths", $projectRoot,
    "--paths", $portableDeps,
    "--paths", (Join-Path $portableDeps "win32"),
    "--collect-all", "av",
    "--hidden-import", "pymobiledevice3.usbmux",
    "--hidden-import", "pymobiledevice3.osu.win_util",
    "--exclude-module", "IPython",
    "--exclude-module", "jedi",
    "--exclude-module", "pygments",
    "--exclude-module", "traitlets",
    "--exclude-module", "tqdm",
    "--exclude-module", "pandas",
    "--exclude-module", "openpyxl",
    "--exclude-module", "win32com",
    "--exclude-module", "pythoncom"
)

$mainMode = if ($ConsoleMain) { "--console" } else { "--windowed" }
$mainPrivileges = @()
if ($AutoUsbRepairTest) {
    # One UAC approval at application startup allows later port repair to stay automatic.
    $mainPrivileges += "--uac-admin"
}
$imeArgs = @(
    "--noconfirm", "--clean",
    "--paths", $projectRoot
)
& $PythonExe -m PyInstaller @commonArgs `
    $mainMode @mainPrivileges --onedir --name $mainName `
    --icon (Join-Path $projectRoot "assets\xinglan.ico") `
    --distpath $distRoot `
    --workpath (Join-Path $workRoot "main-work") `
    --specpath (Join-Path $workRoot "main-spec") `
    (Join-Path $projectRoot "app.py")
if ($LASTEXITCODE -ne 0) { throw "主程序构建失败" }

& $PythonExe -m PyInstaller @imeArgs `
    --console --onedir --contents-directory "_ime_runtime" --name "星澜输入" `
    --distpath $distRoot `
    --workpath (Join-Path $workRoot "ime-work") `
    --specpath (Join-Path $workRoot "ime-spec") `
    (Join-Path $projectRoot "tools\ime_worker_entry.py")
if ($LASTEXITCODE -ne 0) { throw "输入助手构建失败" }

$mainFolder = Join-Path $distRoot $mainName
$imeFolder = Join-Path $distRoot "星澜输入"
Copy-Item -LiteralPath (Join-Path $imeFolder "星澜输入.exe") -Destination $mainFolder -Force
Copy-Item -LiteralPath (Join-Path $imeFolder "_ime_runtime") -Destination $mainFolder -Recurse -Force
Copy-Item -LiteralPath (Join-Path $projectRoot "assets") -Destination $mainFolder -Recurse -Force
if ($E5Optimized) {
    Copy-Item -LiteralPath (Join-Path $projectRoot "profiles\E5_RENDER_PROFILE") -Destination $mainFolder -Force
}
if ($SmoothRenderTest -or $ImeHostAlphaTest -or $GroupPopupUpTest -or $GroupButtonsTest -or $GroupButtonsNoFlickerTest -or $GroupButtonsNoFlickerStopFixTest -or $GroupOneClickTest -or $GroupTwoStepTest -or $GroupTwoStepOtpPasteTest -or $GroupPowerOtpTest -or $TrollVncOtpPasteTest -or $AutoUsbRepairTest -or $PhoneDownload01) {
    Copy-Item -LiteralPath (Join-Path $projectRoot "profiles\SMOOTH_RENDER_PROFILE") -Destination $mainFolder -Force
}
if ($ImeHostAlphaTest -or $GroupPopupUpTest -or $GroupButtonsTest -or $GroupButtonsNoFlickerTest -or $GroupButtonsNoFlickerStopFixTest -or $GroupOneClickTest -or $GroupTwoStepTest -or $GroupTwoStepOtpPasteTest -or $GroupPowerOtpTest -or $TrollVncOtpPasteTest -or $AutoUsbRepairTest -or $PhoneDownload01) {
    Copy-Item -LiteralPath (Join-Path $projectRoot "profiles\IME_HOST_ALPHA_PROFILE") -Destination $mainFolder -Force
}
if ($GroupButtonsTest -or $GroupButtonsNoFlickerTest -or $GroupButtonsNoFlickerStopFixTest -or $GroupOneClickTest -or $GroupTwoStepTest -or $GroupTwoStepOtpPasteTest -or $GroupPowerOtpTest -or $TrollVncOtpPasteTest -or $PhoneDownload01) {
    Copy-Item -LiteralPath (Join-Path $projectRoot "profiles\GROUP_BUTTONS_PROFILE") -Destination $mainFolder -Force
}
if ($GroupButtonsNoFlickerTest -or $GroupButtonsNoFlickerStopFixTest -or $GroupOneClickTest -or $GroupTwoStepTest -or $GroupTwoStepOtpPasteTest -or $GroupPowerOtpTest -or $TrollVncOtpPasteTest -or $PhoneDownload01) {
    Copy-Item -LiteralPath (Join-Path $projectRoot "profiles\FLICKER_FREE_WALL_PROFILE") -Destination $mainFolder -Force
}
if ($GroupOneClickTest) {
    Copy-Item -LiteralPath (Join-Path $projectRoot "profiles\GROUP_ONECLICK_PROFILE") -Destination $mainFolder -Force
}
if ($GroupTwoStepTest -or $GroupTwoStepOtpPasteTest -or $GroupPowerOtpTest -or $TrollVncOtpPasteTest -or $PhoneDownload01) {
    Copy-Item -LiteralPath (Join-Path $projectRoot "profiles\GROUP_TWO_STEP_PROFILE") -Destination $mainFolder -Force
}
if ($GroupTwoStepOtpPasteTest -or $GroupPowerOtpTest -or $TrollVncOtpPasteTest -or $PhoneDownload01) {
    Copy-Item -LiteralPath (Join-Path $projectRoot "profiles\OTP_PASTE_PROFILE") -Destination $mainFolder -Force
}
if ($TrollVncOtpPasteTest) {
    Copy-Item -LiteralPath (Join-Path $projectRoot "profiles\TROLLVNC_OTP_PASTE_PROFILE") -Destination $mainFolder -Force
}
if ($AutoUsbRepairTest) {
    Copy-Item -LiteralPath (Join-Path $projectRoot "profiles\AUTO_USB_REPAIR_PROFILE") -Destination $mainFolder -Force
}
$configTarget = Join-Path $mainFolder "config"
New-Item -ItemType Directory -Path $configTarget -Force | Out-Null
Copy-Item -LiteralPath $configSource -Destination $configTarget -Force
New-Item -ItemType Directory -Path (Join-Path $mainFolder "tools") -Force | Out-Null
Copy-Item -LiteralPath (Join-Path $projectRoot "tools\UsbPortCycle.ps1") -Destination (Join-Path $mainFolder "tools") -Force
$muxTarget = Join-Path $mainFolder "independent-usbmux-test"
New-Item -ItemType Directory -Path $muxTarget -Force | Out-Null
Copy-Item -Path (Join-Path $usbmuxTools "*.dll") -Destination $muxTarget -Force
Copy-Item -LiteralPath (Join-Path $usbmuxTools "idevice_id.exe") -Destination $muxTarget -Force
Copy-Item -LiteralPath (Join-Path $usbmuxTools "iproxy.exe") -Destination $muxTarget -Force
Copy-Item -LiteralPath (Join-Path $projectRoot "TrollVNC触控使用说明.txt") -Destination $mainFolder -Force
Copy-Item -LiteralPath (Join-Path $projectRoot "CURRENT_TROLLVNC_RELEASE") -Destination $mainFolder -Force
$phoneTarget = Join-Path $mainFolder "手机端_RootHide整合插件"
New-Item -ItemType Directory -Path $phoneTarget -Force | Out-Null
Copy-Item -LiteralPath $integratedPackage -Destination $phoneTarget -Force

$finalFolder = Join-Path $releaseRoot $releaseName
Remove-GeneratedPath -Path $finalFolder -AllowedRoot $releaseRoot
Move-Item -LiteralPath $mainFolder -Destination $finalFolder

$zipPath = Join-Path $releaseRoot "$releaseName-Windows.zip"
Remove-GeneratedPath -Path $zipPath -AllowedRoot $releaseRoot
Compress-Archive -LiteralPath $finalFolder -DestinationPath $zipPath -CompressionLevel Optimal
Remove-GeneratedPath -Path $workRoot -AllowedRoot (Join-Path $projectRoot "build")

Write-Output "FOLDER=$finalFolder"
Write-Output "ZIP=$zipPath"
