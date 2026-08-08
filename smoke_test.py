from __future__ import annotations

import sys
import time

from xinglan.diagnostics import working_set_mb
from xinglan.session import DeviceSession


if len(sys.argv) != 2:
    raise SystemExit("usage: smoke_test.py <UDID>")

session = DeviceSession(sys.argv[1])
session.start()
started = time.monotonic()
try:
    while time.monotonic() - started < 12:
        sequence, _, image = session.latest.snapshot()
        stats = session.stats()
        print(
            f"状态={stats.status} 帧={sequence} 帧率={stats.fps:.1f} "
            f"重连={stats.reconnects} 内存={working_set_mb():.1f}MB"
        )
        if sequence >= 8 and image is not None:
            print(f"内存收帧成功：{image.width}x{image.height}，未写入JPEG中间文件")
            break
        time.sleep(1)
    else:
        raise SystemExit(f"在限定时间内没有收到有效画面：{session.stats()}")
finally:
    session.stop()
    time.sleep(0.3)
