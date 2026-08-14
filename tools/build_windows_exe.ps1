param(
    [Parameter(Mandatory = $true)]
    [string]$PythonExe,
    [switch]$ConsoleMain
)

# Windows PowerShell 5.1 requires a BOM to parse Chinese string literals.

$ErrorActionPreference = "Stop"
$projectRoot = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
$workspaceRoot = (Resolve-Path (Join-Path $projectRoot "..\..")).Path
$portableDeps = Join-Path $workspaceRoot "usb_capture_deps"
$usbmuxTools = Join-Path (Split-Path $projectRoot -Parent) "independent-usbmux-test"
$baselineProject = Join-Path (Split-Path $projectRoot -Parent) "星澜_USB原生群控_新版"
$configSource = Join-Path $baselineProject "config\device_groups.json"
$pluginPackages = Join-Path (Split-Path $projectRoot -Parent) "星澜_手机端3种插件_20260730_滑屏0.2秒"
$trollVncSource = Join-Path (Split-Path $projectRoot -Parent) "github-mirror-tests\TrollVNC"
$mainName = "星澜_TrollVNC触控测试"
$releaseName = "星澜_TrollVNC触控测试版"
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
if (-not (Test-Path -LiteralPath $pluginPackages -PathType Container)) {
    throw "缺少 TrollVNC 手机插件目录: $pluginPackages"
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
$imeArgs = @(
    "--noconfirm", "--clean",
    "--paths", $projectRoot
)
& $PythonExe -m PyInstaller @commonArgs `
    $mainMode --onedir --name $mainName `
    --icon (Join-Path $projectRoot "assets\xinglan.ico") `
    --distpath $distRoot `
    --workpath (Join-Path $workRoot "main-work") `
    --specpath (Join-Path $workRoot "main-spec") `
    (Join-Path $projectRoot "app.py")
if ($LASTEXITCODE -ne 0) { throw "主程序构建失败" }

& $PythonExe -m PyInstaller @imeArgs `
    --console --onefile --name "星澜输入" `
    --distpath $distRoot `
    --workpath (Join-Path $workRoot "ime-work") `
    --specpath (Join-Path $workRoot "ime-spec") `
    (Join-Path $projectRoot "tools\ime_worker_entry.py")
if ($LASTEXITCODE -ne 0) { throw "输入助手构建失败" }

$mainFolder = Join-Path $distRoot $mainName
Copy-Item -LiteralPath (Join-Path $distRoot "星澜输入.exe") -Destination $mainFolder -Force
Copy-Item -LiteralPath (Join-Path $projectRoot "assets") -Destination $mainFolder -Recurse -Force
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
Copy-Item -LiteralPath (Join-Path $projectRoot "TrollVNC触控测试说明.txt") -Destination $mainFolder -Force
$pluginTarget = Join-Path $mainFolder "手机端_TrollVNC插件"
New-Item -ItemType Directory -Path $pluginTarget -Force | Out-Null
Copy-Item -LiteralPath (Join-Path $pluginPackages "2_TrollVNC静默_普通越狱版.deb") -Destination $pluginTarget -Force
Copy-Item -LiteralPath (Join-Path $pluginPackages "2_TrollVNC静默_RootHide版.deb") -Destination $pluginTarget -Force
Copy-Item -LiteralPath (Join-Path $trollVncSource "COPYING") -Destination (Join-Path $pluginTarget "TrollVNC_GPLv2_LICENSE.txt") -Force

$finalFolder = Join-Path $releaseRoot $releaseName
if (Test-Path -LiteralPath $finalFolder) {
    $previousFolder = Join-Path $releaseRoot "$releaseName-上次"
    if (Test-Path -LiteralPath $previousFolder) {
        $previousFolder = Join-Path $releaseRoot "$releaseName-上次-$buildStamp"
    }
    Move-Item -LiteralPath $finalFolder -Destination $previousFolder
}
Move-Item -LiteralPath $mainFolder -Destination $finalFolder

$zipPath = Join-Path $releaseRoot "$releaseName-Windows.zip"
if (Test-Path -LiteralPath $zipPath) {
    Move-Item -LiteralPath $zipPath -Destination (Join-Path $releaseRoot "$releaseName-Windows-上次-$buildStamp.zip")
}
Compress-Archive -LiteralPath $finalFolder -DestinationPath $zipPath -CompressionLevel Optimal

Write-Output "FOLDER=$finalFolder"
Write-Output "ZIP=$zipPath"
