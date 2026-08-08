from __future__ import annotations

import asyncio
import sys

from xinglan.bootstrap import configure_dependencies

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
    results = await asyncio.gather(*(probe(sys.argv[1], port) for port in (6000, 6002, 6202)))
    for port, status in results:
        if port == 6000:
            name = "触摸"
        elif port == 6202:
            name = "新IOSurface视频"
        else:
            name = "旧H.264视频"
        print(f"{port} {name}：{status}")


if __name__ == "__main__":
    asyncio.run(main())
