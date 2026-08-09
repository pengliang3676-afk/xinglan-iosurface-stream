from __future__ import annotations

import subprocess
import time
from pathlib import Path


def discover_usb_udids(project_dir: Path) -> list[str]:
    tool = project_dir.parent / "independent-usbmux-test" / "idevice_id.exe"
    if not tool.exists():
        raise FileNotFoundError(f"缺少USB设备工具：{tool}")
    completed = subprocess.run(
        [str(tool), "-l"],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=8,
        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        check=False,
    )
    if completed.returncode not in (0, 1):
        message = (completed.stderr or completed.stdout).strip()
        raise RuntimeError(message or f"USB扫描失败：{completed.returncode}")
    return sorted({line.strip() for line in completed.stdout.splitlines() if line.strip()})


def discover_usb_udids_stable(
    project_dir: Path,
    *,
    attempts: int = 3,
    interval_seconds: float = 0.08,
) -> list[str]:
    """Merge several quick usbmux snapshots to filter transient omissions."""
    discovered: set[str] = set()
    last_error: Exception | None = None
    attempts = max(1, attempts)
    for index in range(attempts):
        try:
            discovered.update(discover_usb_udids(project_dir))
        except Exception as exc:
            last_error = exc
        if index + 1 < attempts:
            time.sleep(max(0.0, interval_seconds))
    if not discovered and last_error is not None:
        raise last_error
    return sorted(discovered)
