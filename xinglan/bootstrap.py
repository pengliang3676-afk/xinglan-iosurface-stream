from __future__ import annotations

import os
import sys
from pathlib import Path


PROJECT_DIR = Path(__file__).resolve().parent.parent
WORKSPACE_DIR = PROJECT_DIR.parent.parent
USB_DEPS = WORKSPACE_DIR / "usb_capture_deps"


def configure_dependencies() -> None:
    """加载工作区里已经验证过的便携依赖，不修改系统环境。"""
    candidates = [USB_DEPS, USB_DEPS / "win32"]
    for candidate in reversed(candidates):
        value = str(candidate)
        if candidate.exists() and value not in sys.path:
            sys.path.insert(0, value)

    dll_dir = USB_DEPS / "pywin32_system32"
    if os.name == "nt" and dll_dir.exists():
        os.add_dll_directory(str(dll_dir))

