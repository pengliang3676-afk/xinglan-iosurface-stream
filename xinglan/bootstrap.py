from __future__ import annotations

import os
import sys
from pathlib import Path


FROZEN = bool(getattr(sys, "frozen", False))
BUNDLE_DIR = Path(getattr(sys, "_MEIPASS", Path(__file__).resolve().parent.parent))
PROJECT_DIR = (
    Path(sys.executable).resolve().parent
    if FROZEN
    else Path(__file__).resolve().parent.parent
)


def _first_existing(*candidates: Path) -> Path:
    for candidate in candidates:
        if candidate.exists():
            return candidate
    return candidates[0]


# Source runs use the verified portable dependencies under D:\星澜.  The EXE
# build places the same resources beside the executable, so runtime paths no
# longer depend on the source tree being present.
USB_DEPS = _first_existing(
    PROJECT_DIR / "usb_capture_deps",
    BUNDLE_DIR / "usb_capture_deps",
    PROJECT_DIR.parent.parent / "usb_capture_deps",
)
USBMUX_TOOLS = _first_existing(
    PROJECT_DIR / "independent-usbmux-test",
    BUNDLE_DIR / "independent-usbmux-test",
    PROJECT_DIR.parent / "independent-usbmux-test",
)


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
