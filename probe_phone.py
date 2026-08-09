from __future__ import annotations

import asyncio
import sys
from pathlib import Path

from xinglan.bootstrap import configure_dependencies
from xinglan.control_protocol import CONTROL_PORT, STATUS_PORT
from xinglan.device_discovery import discover_usb_udids_stable
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
    names = {
        DeviceSession.VIDEO_PORT: "IOSurface H.264 视频",
        CONTROL_PORT: "二进制控制",
        STATUS_PORT: "状态/心跳",
    }
    if len(sys.argv) != 2:
        raise SystemExit("usage: probe_phone.py <UDID|--all>")
    if sys.argv[1] != "--all":
        results = await asyncio.gather(*(probe(sys.argv[1], port) for port in names))
        for port, status in results:
            print(f"{port} {names[port]}：{status}")
        return

    project_dir = Path(__file__).resolve().parent
    udids = await asyncio.to_thread(discover_usb_udids_stable, project_dir)
    semaphore = asyncio.Semaphore(12)

    async def probe_device(udid: str) -> tuple[str, dict[int, str]]:
        async with semaphore:
            results = await asyncio.gather(*(probe(udid, port) for port in names))
        return udid, dict(results)

    device_results = await asyncio.gather(*(probe_device(udid) for udid in udids))
    ready_counts = {port: 0 for port in names}
    for udid, results in device_results:
        flags = []
        for port, name in names.items():
            ready = results[port] == "可连接"
            ready_counts[port] += int(ready)
            flags.append(f"{name}={'OK' if ready else 'FAIL'}")
        print(f"{udid} " + " ".join(flags))
    print(f"USB={len(udids)} " + " ".join(
        f"{names[port]}={ready_counts[port]}" for port in names
    ))


if __name__ == "__main__":
    asyncio.run(main())
