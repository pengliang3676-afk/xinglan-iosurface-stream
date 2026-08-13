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

from .usb_connection import ServiceConnection  # noqa: E402


FILE_SERVICE_PORT = FILE_PORT
MAX_FILE_SIZE = 8 * 1024 * 1024 * 1024
CHUNK_SIZE = 256 * 1024
MAX_FOLDER_ENTRIES = 100_000
MAX_FOLDER_MANIFEST_SIZE = 16 * 1024 * 1024


@dataclass(frozen=True)
class FileTransferResult:
    success: bool
    message: str = ""


@dataclass(frozen=True)
class PreparedTransfer:
    header: bytes
    metadata: bytes
    files: tuple[Path, ...]


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


def build_folder_transfer_header(
    path: Path,
) -> tuple[bytes, bytes, list[tuple[Path, int]]]:
    root = Path(path)
    if not root.is_dir():
        raise ValueError("所选文件夹不存在")
    if root.is_symlink():
        raise ValueError("不能传输符号链接文件夹")

    entries: list[dict[str, object]] = []
    files: list[tuple[Path, int]] = []
    total_size = 0
    for item in sorted(root.rglob("*"), key=lambda value: value.as_posix().lower()):
        if item.is_symlink():
            raise ValueError(f"文件夹包含符号链接：{item.name}")
        relative = item.relative_to(root).as_posix()
        if not relative or relative.startswith("/") or ".." in Path(relative).parts:
            raise ValueError("文件夹包含无效路径")
        if item.is_dir():
            entries.append({"path": relative, "directory": True})
            if len(entries) > MAX_FOLDER_ENTRIES:
                raise ValueError("文件夹内容超过 100000 项")
            continue
        if not item.is_file():
            continue
        size = item.stat().st_size
        total_size += size
        if total_size > MAX_FILE_SIZE:
            raise ValueError("文件夹内容合计超过 8GB")
        entries.append({"path": relative, "size": size})
        files.append((item, size))
        if len(entries) > MAX_FOLDER_ENTRIES:
            raise ValueError("文件夹内容超过 100000 项")

    metadata = json.dumps(
        {"name": root.name, "entries": entries},
        ensure_ascii=False,
        separators=(",", ":"),
    ).encode("utf-8")
    if len(metadata) > MAX_FOLDER_MANIFEST_SIZE:
        raise ValueError("文件夹目录清单过大")
    header = b"XLFD" + struct.pack(">IQ", len(metadata), total_size)
    return header, metadata, files


def prepare_transfer(source: Path, import_photo: bool = False) -> PreparedTransfer:
    path = Path(source)
    if path.is_dir():
        header, metadata, folder_files = build_folder_transfer_header(path)
        return PreparedTransfer(
            header=header,
            metadata=metadata,
            files=tuple(file_path for file_path, _size in folder_files),
        )
    header, metadata = build_transfer_header(path, import_photo)
    return PreparedTransfer(header=header, metadata=metadata, files=(path,))


async def _send_path_payload(connection: Any, source: Path) -> None:
    path = Path(source)
    with path.open("rb") as source_file:
        while True:
            chunk = await asyncio.to_thread(source_file.read, CHUNK_SIZE)
            if not chunk:
                break
            await asyncio.wait_for(connection.sendall(chunk), timeout=60.0)


async def send_file_to_device(
    udid: str,
    source: Path,
    import_photo: bool = False,
) -> FileTransferResult:
    path = Path(source)
    try:
        prepared = prepare_transfer(path, import_photo)
    except FileNotFoundError:
        return FileTransferResult(False, "电脑文件或文件夹不存在")
    except ValueError as exc:
        return FileTransferResult(False, str(exc))
    return await _send_prepared_to_device(udid, prepared)


async def _send_prepared_to_device(
    udid: str,
    prepared: PreparedTransfer,
) -> FileTransferResult:
    connection: Any | None = None
    try:
        connection = await asyncio.wait_for(
            ServiceConnection.create_using_usbmux(
                udid,
                FILE_SERVICE_PORT,
                connection_type="USB",
            ),
            timeout=12.0,
        )
        await asyncio.wait_for(connection.sendall(prepared.header), timeout=30.0)
        await asyncio.wait_for(connection.sendall(prepared.metadata), timeout=30.0)
        for source_file in prepared.files:
            await _send_path_payload(connection, source_file)

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
        return FileTransferResult(False, "电脑文件或文件夹不存在")
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
    try:
        prepared = prepare_transfer(Path(source), import_photo)
    except FileNotFoundError:
        return {
            udid: FileTransferResult(False, "电脑文件或文件夹不存在")
            for udid in ordered
        }
    except ValueError as exc:
        return {udid: FileTransferResult(False, str(exc)) for udid in ordered}
    results = await asyncio.gather(
        *(_send_prepared_to_device(udid, prepared) for udid in ordered),
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
