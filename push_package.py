from __future__ import annotations

import argparse
import asyncio
from pathlib import Path, PurePosixPath

from xinglan.bootstrap import configure_dependencies

configure_dependencies()

from pymobiledevice3.lockdown import create_using_usbmux  # noqa: E402
from pymobiledevice3.services.afc import AfcService  # noqa: E402


async def push(udid: str, package: Path) -> str:
    remote_dir = "Downloads"
    remote_path = str(PurePosixPath(remote_dir, package.name))
    async with await create_using_usbmux(
        serial=udid,
        connection_type="USB",
    ) as lockdown:
        async with AfcService(lockdown=lockdown) as afc:
            if not await afc.exists(remote_dir):
                await afc.makedirs(remote_dir)
            await afc.push(str(package), remote_path, progress_bar=False)
            info = await afc.stat(remote_path)
            remote_size = int(info["st_size"])
    if remote_size != package.stat().st_size:
        raise RuntimeError(
            f"传输后大小不一致：电脑 {package.stat().st_size}，手机 {remote_size}"
        )
    return f"/var/mobile/Media/{remote_path}"


async def main() -> None:
    parser = argparse.ArgumentParser(description="通过 USB 把 XLStream deb 复制到指定测试机")
    parser.add_argument("udid")
    parser.add_argument("package", type=Path)
    args = parser.parse_args()
    package = args.package.resolve()
    if not package.is_file():
        raise SystemExit(f"安装包不存在：{package}")
    remote_path = await push(args.udid, package)
    print(f"已复制到指定手机：{remote_path}")


if __name__ == "__main__":
    asyncio.run(main())
