from __future__ import annotations

import base64
import json
import os
import subprocess
import tempfile
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Iterable

from xinglan.device_discovery import discover_usb_udids_stable


CREATE_NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)
IPHONE_COMPOSITE_PREFIX = "USB\\VID_05AC&PID_12A8\\"


def normalize_udid(value: str) -> str:
    """Return the comparable serial form used by Windows PnP and usbmux."""
    return "".join(character for character in value.upper() if character.isalnum())


@dataclass(frozen=True)
class WindowsIphone:
    serial: str
    parent_instance_id: str
    parent_name: str


@dataclass(frozen=True)
class UsbRepairBranch:
    target_instance_id: str
    target_name: str
    missing_serials: tuple[str, ...]


@dataclass(frozen=True)
class UsbRepairPlan:
    windows_count: int
    usbmux_count: int
    missing_serials: tuple[str, ...]
    branches: tuple[UsbRepairBranch, ...]
    unsupported_serials: tuple[str, ...]


@dataclass(frozen=True)
class UsbRepairResult:
    before_count: int
    after_count: int
    windows_count: int
    cycled_branches: int
    missing_before: tuple[str, ...]


def build_repair_plan(
    usbmux_udids: Iterable[str],
    windows_iphones: Iterable[WindowsIphone],
) -> UsbRepairPlan:
    phones = tuple(windows_iphones)
    online = {normalize_udid(udid) for udid in usbmux_udids}
    missing = tuple(phone for phone in phones if normalize_udid(phone.serial) not in online)

    grouped: dict[tuple[str, str], list[str]] = {}
    unsupported: list[str] = []
    for phone in missing:
        target_id = phone.parent_instance_id.strip()
        # Only cycle an external USB hub. Cycling a controller/root hub could
        # disconnect every phone and unrelated USB peripherals on the PC.
        if not target_id.upper().startswith("USB\\VID_"):
            unsupported.append(phone.serial)
            continue
        grouped.setdefault((target_id, phone.parent_name), []).append(phone.serial)

    branches = tuple(
        UsbRepairBranch(
            target_instance_id=target_id,
            target_name=target_name,
            missing_serials=tuple(sorted(serials)),
        )
        for (target_id, target_name), serials in sorted(grouped.items())
    )
    return UsbRepairPlan(
        windows_count=len(phones),
        usbmux_count=len(online),
        missing_serials=tuple(sorted(phone.serial for phone in missing)),
        branches=branches,
        unsupported_serials=tuple(sorted(unsupported)),
    )


def _powershell_json(command: str, *, timeout: float = 45.0) -> object:
    completed = subprocess.run(
        [
            "powershell.exe",
            "-NoProfile",
            "-NonInteractive",
            "-ExecutionPolicy",
            "Bypass",
            "-Command",
            command,
        ],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=timeout,
        creationflags=CREATE_NO_WINDOW,
        check=False,
    )
    if completed.returncode != 0:
        message = (completed.stderr or completed.stdout).strip()
        raise RuntimeError(message or f"Windows USB枚举失败：{completed.returncode}")
    payload = completed.stdout.strip()
    return json.loads(payload) if payload else []


def discover_windows_iphones(
    online_udids: Iterable[str] = (),
) -> list[WindowsIphone]:
    """Enumerate the iPhones that Windows PnP currently considers present."""
    normalized_online = sorted({normalize_udid(udid) for udid in online_udids})
    powershell_online = ",".join(f"'{udid}'" for udid in normalized_online)
    command = r"""
$ErrorActionPreference = 'Stop'
$online = @(__XINGLAN_ONLINE_UDIDS__)
$allDevices = @(Get-PnpDevice -PresentOnly)
$deviceById = @{}
foreach ($device in $allDevices) {
    $deviceById[$device.InstanceId] = $device
}
$rows = @(
    $allDevices |
        Where-Object { $_.InstanceId -like 'USB\VID_05AC&PID_12A8\*' } |
        ForEach-Object {
            $serial = ($_.InstanceId -split '\\')[-1]
            $parentId = ''
            $parent = $null
            if ($online -notcontains $serial.ToUpperInvariant()) {
                $parentId = (Get-PnpDeviceProperty -InstanceId $_.InstanceId -KeyName 'DEVPKEY_Device_Parent').Data
                $parent = $deviceById[[string]$parentId]
            }
            [pscustomobject]@{
                Serial = $serial
                ParentInstanceId = [string]$parentId
                ParentName = [string]$parent.FriendlyName
            }
        }
)
ConvertTo-Json -InputObject $rows -Compress
""".replace("__XINGLAN_ONLINE_UDIDS__", powershell_online)
    payload = _powershell_json(command)
    if isinstance(payload, dict):
        payload = [payload]
    records: list[WindowsIphone] = []
    for item in payload if isinstance(payload, list) else []:
        if not isinstance(item, dict):
            continue
        serial = str(item.get("Serial", "")).strip()
        if not serial:
            continue
        records.append(
            WindowsIphone(
                serial=serial,
                parent_instance_id=str(item.get("ParentInstanceId", "")).strip(),
                parent_name=str(item.get("ParentName", "")).strip(),
            )
        )
    return records


def discover_windows_iphone_count() -> int:
    """Return only the present iPhone count for the lightweight 30s monitor."""
    command = r"""
$ErrorActionPreference = 'Stop'
$count = @(
    Get-PnpDevice -PresentOnly |
        Where-Object { $_.InstanceId -like 'USB\VID_05AC&PID_12A8\*' }
).Count
ConvertTo-Json -InputObject $count -Compress
"""
    return max(0, int(_powershell_json(command)))


def detect_usb_repair_plan(project_dir: Path) -> UsbRepairPlan:
    usbmux_udids = discover_usb_udids_stable(project_dir)
    return build_repair_plan(usbmux_udids, discover_windows_iphones(usbmux_udids))


def _encoded_powershell(command: str) -> str:
    return base64.b64encode(command.encode("utf-16-le")).decode("ascii")


def cycle_usb_branches(project_dir: Path, branches: Iterable[UsbRepairBranch]) -> int:
    targets = tuple(dict.fromkeys(branch.target_instance_id for branch in branches))
    if not targets:
        return 0

    helper = project_dir / "tools" / "UsbPortCycle.ps1"
    if not helper.is_file():
        raise FileNotFoundError(f"缺少USB修复组件：{helper}")

    target_path = Path(tempfile.gettempdir()) / f"xinglan-usb-targets-{os.getpid()}.txt"
    result_path = Path(tempfile.gettempdir()) / f"xinglan-usb-result-{os.getpid()}.json"
    target_path.write_text("\n".join(targets), encoding="utf-8")
    result_path.unlink(missing_ok=True)
    try:
        arguments = (
            f"-NoProfile -NonInteractive -ExecutionPolicy Bypass "
            f"-File \"{helper}\" -TargetFile \"{target_path}\" "
            f"-ResultFile \"{result_path}\" -Cycle"
        )
        launcher = (
            f"$process = Start-Process -FilePath 'powershell.exe' "
            f"-ArgumentList '{arguments.replace("'", "''")}' "
            f"-Verb RunAs -WindowStyle Hidden -Wait -PassThru; "
            f"exit $process.ExitCode"
        )
        completed = subprocess.run(
            ["powershell.exe", "-NoProfile", "-EncodedCommand", _encoded_powershell(launcher)],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=90,
            creationflags=CREATE_NO_WINDOW,
            check=False,
        )
        if completed.returncode != 0:
            if completed.returncode == 1223:
                raise RuntimeError("已取消Windows管理员确认")
            details = ""
            if result_path.is_file():
                details = result_path.read_text(encoding="utf-8", errors="replace").strip()
            message = details or (completed.stderr or completed.stdout).strip()
            raise RuntimeError(message or f"USB端口闪断失败：{completed.returncode}")
        return len(targets)
    finally:
        target_path.unlink(missing_ok=True)
        result_path.unlink(missing_ok=True)


def repair_usb(
    project_dir: Path,
    *,
    timeout_seconds: float = 18.0,
    progress: Callable[[str], None] | None = None,
) -> UsbRepairResult:
    plan = detect_usb_repair_plan(project_dir)
    if not plan.missing_serials:
        return UsbRepairResult(
            before_count=plan.usbmux_count,
            after_count=plan.usbmux_count,
            windows_count=plan.windows_count,
            cycled_branches=0,
            missing_before=(),
        )
    if plan.windows_count > 0 and plan.usbmux_count == 0:
        raise RuntimeError("投屏USB通道完全离线，为避免同时闪断全部手机，已停止修复")
    if not plan.branches:
        suffixes = "、".join(serial[-8:] for serial in plan.unsupported_serials)
        raise RuntimeError(f"缺失手机不在可安全闪断的外接Hub上：{suffixes}")

    if progress is not None:
        progress(
            f"检测到 {len(plan.missing_serials)} 台缺失，正在闪断 {len(plan.branches)} 个USB分支…"
        )
    cycled = cycle_usb_branches(project_dir, plan.branches)

    deadline = time.monotonic() + max(1.0, timeout_seconds)
    after: list[str] = []
    while time.monotonic() < deadline:
        try:
            after = discover_usb_udids_stable(
                project_dir,
                attempts=2,
                interval_seconds=0.12,
            )
        except Exception:
            after = []
        if len(after) >= plan.windows_count:
            break
        time.sleep(0.45)

    return UsbRepairResult(
        before_count=plan.usbmux_count,
        after_count=len({normalize_udid(udid) for udid in after}),
        windows_count=plan.windows_count,
        cycled_branches=cycled,
        missing_before=plan.missing_serials,
    )
