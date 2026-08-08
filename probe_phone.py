from __future__ import annotations

import asyncio
import sys

from xinglan.bootstrap import configure_dependencies
from xinglan.control_protocol import CONTROL_PORT, STATUS_PORT
from xinglan.session import DeviceSession

configure_dependencies()

from pymobiledevice3.service_connection import ServiceConnection


async def probe(udid: str, port: int) -> tuple[int, str]:
    connection = None
    try:
        connection = await asyncio.wait_for(
            ServiceConnection.create_using_usbmux(udid, port, connection_type="USB"),
            timeout=4.0,
        )
        return port, "可连接"
    except Exception as exc:
        return port, f"不可连接：{exc}"
    finally:
        if connection is not None:
            try:
                await connection.close()
            except Exception:
                pass


async def main() -> None:
    if len(sys.argv) != 2:
        raise SystemExit("usage: probe_phone.py <UDID>")
    names = {
        DeviceSession.VIDEO_PORT: "IOSurface H.264 视频",
        CONTROL_PORT: "二进制控制",
        STATUS_PORT: "状态/心跳",
    }
    results = await asyncio.gather(*(probe(sys.argv[1], port) for port in names))
    for port, status in results:
        print(f"{port} {names[port]}：{status}")


if __name__ == "__main__":
    asyncio.run(main())
