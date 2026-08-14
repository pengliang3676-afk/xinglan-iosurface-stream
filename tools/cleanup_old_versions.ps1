# PowerShell 5.1-safe cleanup for generated Xinglan artifacts. The script is
# intentionally ASCII-only because Windows PowerShell parses UTF-8 scripts
# without a BOM using the legacy system code page.

$ErrorActionPreference = "Stop"

$experimentRoot = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
$projectsRoot = Split-Path $experimentRoot -Parent
$formalCandidates = @(
    Get-ChildItem -LiteralPath $projectsRoot -Directory -Force |
        Where-Object {
            $_.Name -like "*_USB*" -and
            (Test-Path -LiteralPath (Join-Path $_.FullName ".git"))
        }
)
if ($formalCandidates.Count -ne 1) {
    throw "Expected exactly one formal USB project root"
}
$formalRoot = $formalCandidates[0].FullName

$experimentRelease = Join-Path $experimentRoot "release"
$releaseFolders = @(
    Get-ChildItem -LiteralPath $experimentRelease -Directory -Force |
        Where-Object { $_.Name -like "*TrollVNC*" }
)
$releaseZips = @(
    Get-ChildItem -LiteralPath $experimentRelease -File -Force |
        Where-Object {
            $_.Name -like "*TrollVNC*Windows*.zip"
        }
)
$currentReleaseCandidates = @(
    $releaseFolders |
        Where-Object {
            Test-Path -LiteralPath (Join-Path $_.FullName "CURRENT_TROLLVNC_RELEASE")
        }
)
if ($currentReleaseCandidates.Count -ne 1) {
    throw "Expected exactly one marked TrollVNC release"
}
$currentReleaseFolder = $currentReleaseCandidates[0].FullName
$currentReleaseZip = Join-Path $experimentRelease (
    $currentReleaseCandidates[0].Name + "-Windows.zip"
)
if (-not (Test-Path -LiteralPath $currentReleaseZip -PathType Leaf)) {
    throw "The adopted TrollVNC release is missing"
}

$backupRoot = Join-Path $formalRoot "backups"
$backupFolders = @(
    Get-ChildItem -LiteralPath $backupRoot -Directory -Force |
        ForEach-Object {
            $size = (
                Get-ChildItem -LiteralPath $_.FullName -File -Recurse -Force -ErrorAction SilentlyContinue |
                    Measure-Object -Property Length -Sum
            ).Sum
            if ($null -eq $size) { $size = 0 }
            [pscustomobject]@{
                FullName = $_.FullName
                Size = [int64]$size
            }
        } |
        Sort-Object Size -Descending
)
if ($backupFolders.Count -lt 1) {
    throw "The protected rollback backup is missing"
}
$currentBackup = $backupFolders[0].FullName
$deviceMap = Join-Path $formalRoot "config\device_groups.json"
$gitRoot = Join-Path $formalRoot ".git"

$targets = @(
    (Join-Path $formalRoot "release"),
    (Join-Path $formalRoot "build"),
    (Join-Path $experimentRoot "build")
)
$targets += @(
    $releaseFolders |
        Where-Object { $_.FullName -ne $currentReleaseFolder } |
        ForEach-Object { $_.FullName }
)
$targets += @(
    $releaseZips |
        Where-Object { $_.FullName -ne $currentReleaseZip } |
        ForEach-Object { $_.FullName }
)
if ($backupFolders.Count -gt 1) {
    $targets += @($backupFolders | Select-Object -Skip 1 | ForEach-Object { $_.FullName })
}

$protected = @(
    $currentReleaseFolder,
    $currentReleaseZip,
    $currentBackup,
    $deviceMap,
    $gitRoot
) | ForEach-Object { [IO.Path]::GetFullPath($_) }

function Test-IsWithinRoot {
    param(
        [Parameter(Mandatory = $true)][string]$Path,
        [Parameter(Mandatory = $true)][string]$Root
    )
    $prefix = $Root.TrimEnd('\') + '\'
    return $Path.StartsWith($prefix, [StringComparison]::OrdinalIgnoreCase)
}

$validated = @()
foreach ($target in $targets) {
    $full = [IO.Path]::GetFullPath($target)
    $withinAllowedRoot =
        (Test-IsWithinRoot -Path $full -Root $formalRoot) -or
        (Test-IsWithinRoot -Path $full -Root $experimentRoot)
    if (-not $withinAllowedRoot) {
        throw "Deletion target escaped the allowed project roots: $full"
    }
    foreach ($keep in $protected) {
        $targetPrefix = $full.TrimEnd('\') + '\'
        if (
            $keep.Equals($full, [StringComparison]::OrdinalIgnoreCase) -or
            $keep.StartsWith($targetPrefix, [StringComparison]::OrdinalIgnoreCase)
        ) {
            throw "Deletion target contains a protected path: $full"
        }
    }
    if (Test-Path -LiteralPath $full) {
        $validated += $full
    }
}

$bytesBefore = 0L
foreach ($full in $validated) {
    if (Test-Path -LiteralPath $full -PathType Leaf) {
        $bytesBefore += (Get-Item -LiteralPath $full).Length
        continue
    }
    $value = (
        Get-ChildItem -LiteralPath $full -File -Recurse -Force -ErrorAction SilentlyContinue |
            Measure-Object -Property Length -Sum
    ).Sum
    if ($null -ne $value) {
        $bytesBefore += [int64]$value
    }
}

$deletionPrefixes = @(
    $validated |
        Where-Object { Test-Path -LiteralPath $_ -PathType Container } |
        ForEach-Object { ([IO.Path]::GetFullPath($_)).TrimEnd('\') + '\' }
)
$oldProcesses = @(
    Get-CimInstance Win32_Process |
        Where-Object {
            if (-not $_.ExecutablePath) { return $false }
            $processPath = [IO.Path]::GetFullPath($_.ExecutablePath)
            foreach ($prefix in $deletionPrefixes) {
                if ($processPath.StartsWith($prefix, [StringComparison]::OrdinalIgnoreCase)) {
                    return $true
                }
            }
            return $false
        }
)
foreach ($item in $oldProcesses) {
    $process = Get-Process -Id $item.ProcessId -ErrorAction SilentlyContinue
    if ($null -ne $process) {
        $null = $process.CloseMainWindow()
    }
}
foreach ($item in $oldProcesses) {
    try {
        Wait-Process -Id $item.ProcessId -Timeout 5 -ErrorAction Stop
    } catch {
        Stop-Process -Id $item.ProcessId -Force -ErrorAction SilentlyContinue
    }
}

foreach ($full in $validated) {
    Remove-Item -LiteralPath $full -Recurse -Force
}

foreach ($keep in $protected) {
    if (-not (Test-Path -LiteralPath $keep)) {
        throw "A protected path is missing after cleanup: $keep"
    }
}

[pscustomobject]@{
    DeletedTargets = $validated
    FreedBytes = $bytesBefore
    FreedGiB = [math]::Round($bytesBefore / 1GB, 3)
    Protected = $protected
    ClosedOldProcesses = @($oldProcesses | ForEach-Object { $_.ProcessId })
} | ConvertTo-Json -Depth 5
