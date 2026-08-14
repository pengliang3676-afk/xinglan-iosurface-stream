param(
    [Parameter(Mandatory = $true)]
    [string]$PythonExe,
    [switch]$ConsoleMain,
    [switch]$E5Optimized
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
$mainName = if ($E5Optimized) { "星澜_双路E5" } else { "星澜" }
$releaseName = if ($E5Optimized) { "星澜_双路E5优化版" } else { "星澜_TrollVNC触控版" }

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
if ($E5Optimized) {
    Copy-Item -LiteralPath (Join-Path $projectRoot "profiles\E5_RENDER_PROFILE") -Destination $mainFolder -Force
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
