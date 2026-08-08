from __future__ import annotations

import asyncio
import json
import struct
from collections.abc import Iterable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .bootstrap import configure_dependencies
from .control_protocol import FILE_PORT


configure_dependencies()

from pymobiledevice3.service_connection import ServiceConnection  # noqa: E402


FILE_SERVICE_PORT = FILE_PORT
MAX_FILE_SIZE = 8 * 1024 * 1024 * 1024
CHUNK_SIZE = 256 * 1024


@dataclass(frozen=True)
class FileTransferResult:
    success: bool
    message: str = ""


def build_transfer_header(path: Path, import_photo: bool) -> tuple[bytes, bytes]:
    size = path.stat().st_size
    if size <= 0:
        raise ValueError("不能传输空文件")
    if size > MAX_FILE_SIZE:
        raise ValueError("文件超过 8GB")
    metadata = json.dumps(
        {"name": path.name, "importPhoto": bool(import_photo)},
        ensure_ascii=False,
        separators=(",", ":"),
    ).encode("utf-8")
    header = b"XLFT" + struct.pack(">IQ", len(metadata), size)
    return header, metadata


async def send_file_to_device(
    udid: str,
    source: Path,
    import_photo: bool = False,
) -> FileTransferResult:
    path = Path(source)
    connection: Any | None = None
    try:
        header, metadata = build_transfer_header(path, import_photo)
        connection = await asyncio.wait_for(
            ServiceConnection.create_using_usbmux(
                udid,
                FILE_SERVICE_PORT,
                connection_type="USB",
            ),
            timeout=12.0,
        )
        await asyncio.wait_for(connection.sendall(header), timeout=30.0)
        await asyncio.wait_for(connection.sendall(metadata), timeout=30.0)
        with path.open("rb") as source_file:
            while True:
                chunk = await asyncio.to_thread(source_file.read, CHUNK_SIZE)
                if not chunk:
                    break
                await asyncio.wait_for(connection.sendall(chunk), timeout=60.0)

        response = bytearray()
        while b"\n" not in response:
            chunk = await asyncio.wait_for(connection.recv_any(4096), timeout=600.0)
            if not chunk:
                return FileTransferResult(False, "传输连接已断开")
            response.extend(chunk)
            if len(response) > 64 * 1024:
                return FileTransferResult(False, "手机返回的数据过大")
        payload = json.loads(bytes(response).split(b"\n", 1)[0].decode("utf-8"))
        return FileTransferResult(
            bool(payload.get("success")),
            str(payload.get("message", "")),
        )
    except FileNotFoundError:
        return FileTransferResult(False, "电脑文件不存在")
    except ValueError as exc:
        return FileTransferResult(False, str(exc))
    except asyncio.TimeoutError:
        return FileTransferResult(False, "传输超时")
    except (OSError, json.JSONDecodeError, UnicodeDecodeError):
        return FileTransferResult(False, "手机文件服务未连接或返回无法识别")
    except Exception as exc:
        return FileTransferResult(False, str(exc) or "文件传输失败")
    finally:
        if connection is not None:
            try:
                await connection.close()
            except Exception:
                pass


async def send_file_to_devices(
    udids: Iterable[str],
    source: Path,
    import_photo: bool = False,
) -> dict[str, FileTransferResult]:
    ordered = list(dict.fromkeys(udids))
    results = await asyncio.gather(
        *(
            send_file_to_device(udid, source, import_photo)
            for udid in ordered
        ),
        return_exceptions=True,
    )
    return {
        udid: (
            result
            if isinstance(result, FileTransferResult)
            else FileTransferResult(False, str(result) or "文件传输失败")
        )
        for udid, result in zip(ordered, results)
    }
