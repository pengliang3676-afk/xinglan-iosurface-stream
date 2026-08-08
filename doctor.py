from __future__ import annotations

from xinglan.bootstrap import PROJECT_DIR, USB_DEPS, configure_dependencies

configure_dependencies()

import av
from PIL import Image, ImageTk
from pymobiledevice3.service_connection import ServiceConnection

from xinglan.device_discovery import discover_usb_udids
from xinglan.diagnostics import working_set_mb


print(f"项目目录：{PROJECT_DIR}")
print(f"USB依赖：{USB_DEPS}")
print(f"PyAV：{av.__version__}")
print("Pillow：正常")
print("USB连接库：正常")
print(f"进程内存检测：{working_set_mb():.1f} MB")
devices = discover_usb_udids(PROJECT_DIR)
print(f"当前USB手机：{len(devices)} 台")
for device in devices:
    print(f"  {device}")
