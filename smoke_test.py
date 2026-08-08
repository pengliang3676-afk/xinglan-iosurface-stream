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
    while time.monotonic() - started < 20:
        sequence, _, image = session.latest.snapshot()
        stats = session.stats()
        print(
            f"状态={stats.status} 帧={sequence} 帧率={stats.fps:.1f} "
            f"控制={'在线' if stats.control_online else '离线'} "
            f"心跳={'在线' if stats.status_online else '离线'} "
            f"重连={stats.reconnects} 内存={working_set_mb():.1f}MB"
        )
        if (
            sequence >= 8
            and image is not None
            and stats.control_online
            and stats.status_online
        ):
            print(
                f"三通道验证成功：{image.width}x{image.height}，"
                f"电量={stats.battery_percent}%，手机丢帧={stats.phone_dropped_frames}"
            )
            break
        time.sleep(1)
    else:
        raise SystemExit(f"限定时间内三通道没有全部就绪：{session.stats()}")
finally:
    session.stop()
    time.sleep(0.3)
