from __future__ import annotations

import subprocess
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

