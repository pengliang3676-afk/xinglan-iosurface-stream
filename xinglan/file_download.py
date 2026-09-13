from __future__ import annotations

import asyncio
import json
import struct
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .bootstrap import configure_dependencies
from .control_protocol import FILE_PORT

configure_dependencies()

from .usb_connection import ServiceConnection  # noqa: E402

DOWNLOAD_SERVICE_PORT = FILE_PORT
MAX_MANIFEST_SIZE = 16 * 1024 * 1024
MAX_FOLDER_ENTRIES = 100_000
CHUNK_SIZE = 256 * 1024


@dataclass(frozen=True)
class DirectoryEntry:
    name: str
    directory: bool
    size: int


@dataclass(frozen=True)
class ListResult:
    success: bool
    message: str = ""
    path: str = ""
    entries: tuple[DirectoryEntry, ...] = ()


@dataclass(frozen=True)
class DownloadResult:
    success: bool
    message: str = ""
    saved_path: str = ""
    total_files: int = 0
    total_bytes: int = 0


def _build_request(magic: bytes, path: str) -> tuple[bytes, bytes]:
    metadata = json.dumps({"path": path}, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    header = magic + struct.pack(">IQ", len(metadata), 0)
    return header, metadata


async def _recv_exact(connection: Any, length: int) -> bytes:
    chunks: list[bytes] = []
    remaining = length
    while remaining > 0:
        chunk = await asyncio.wait_for(connection.recv_any(min(CHUNK_SIZE, remaining)), timeout=60.0)
        if not chunk:
            raise ConnectionError("USB连接已断开")
        chunks.append(chunk)
        remaining -= len(chunk)
    return b"".join(chunks)


async def _recv_json_line(connection: Any, *, max_bytes: int = 64 * 1024) -> dict[str, Any]:
    response = bytearray()
    while b"\n" not in response:
        chunk = await asyncio.wait_for(connection.recv_any(4096), timeout=120.0)
        if not chunk:
            raise ConnectionError("USB连接已断开")
        response.extend(chunk)
        if len(response) > max_bytes:
            raise ValueError("手机返回的数据过大")
    line = bytes(response).split(b"\n", 1)[0]
    return json.loads(line.decode("utf-8"))


async def list_directory(udid: str, path: str) -> ListResult:
    connection: Any | None = None
    try:
        connection = await asyncio.wait_for(
            ServiceConnection.create_using_usbmux(udid, DOWNLOAD_SERVICE_PORT, connection_type="USB"),
            timeout=12.0,
        )
        header, metadata = _build_request(b"XLLS", path)
        await asyncio.wait_for(connection.sendall(header), timeout=30.0)
        await asyncio.wait_for(connection.sendall(metadata), timeout=30.0)

        payload = await _recv_json_line(connection, max_bytes=MAX_MANIFEST_SIZE)
        if not payload.get("success"):
            return ListResult(False, str(payload.get("message", "列目录失败")), path)

        raw_entries = payload.get("entries") or []
        entries: list[DirectoryEntry] = []
        for item in raw_entries:
            if not isinstance(item, dict):
                continue
            entries.append(
                DirectoryEntry(
                    name=str(item.get("name", "")),
                    directory=bool(item.get("directory", False)),
                    size=int(item.get("size", 0) or 0),
                )
            )
        return ListResult(True, path=path, entries=tuple(entries))
    except asyncio.TimeoutError:
        return ListResult(False, "连接超时")
    except (OSError, json.JSONDecodeError, UnicodeDecodeError, ValueError, ConnectionError) as exc:
        return ListResult(False, str(exc) or "手机文件服务未连接或返回无法识别")
    except Exception as exc:
        return ListResult(False, str(exc) or "列目录失败")
    finally:
        if connection is not None:
            try:
                await connection.close()
            except Exception:
                pass


async def download_folder(udid: str, phone_path: str, save_dir: Path) -> DownloadResult:
    connection: Any | None = None
    try:
        save_root = Path(save_dir)
        if not save_root.exists():
            save_root.mkdir(parents=True, exist_ok=True)

        connection = await asyncio.wait_for(
            ServiceConnection.create_using_usbmux(udid, DOWNLOAD_SERVICE_PORT, connection_type="USB"),
            timeout=12.0,
        )
        header, metadata = _build_request(b"XLDW", phone_path)
        await asyncio.wait_for(connection.sendall(header), timeout=30.0)
        await asyncio.wait_for(connection.sendall(metadata), timeout=30.0)

        # 1. 读清单长度（4字节大端）
        manifest_len_bytes = await _recv_exact(connection, 4)
        manifest_len = struct.unpack(">I", manifest_len_bytes)[0]
        if manifest_len == 0 or manifest_len > MAX_MANIFEST_SIZE:
            return DownloadResult(False, "手机返回的目录清单大小无效")

        # 2. 读清单 JSON
        manifest_data = await _recv_exact(connection, manifest_len)
        manifest = json.loads(manifest_data.decode("utf-8"))
        folder_name = str(manifest.get("name", "") or "downloaded_folder")
        raw_entries = manifest.get("entries") or []
        total_size = int(manifest.get("total_size", 0) or 0)

        if not isinstance(raw_entries, list) or len(raw_entries) > MAX_FOLDER_ENTRIES:
            return DownloadResult(False, "手机返回的目录清单无效")

        # 3. 在保存目录下创建以文件夹名命名的子目录
        safe_name = "".join(c for c in folder_name if c not in '\\/:*?"<>|').strip() or "downloaded_folder"
        dest_root = save_root / safe_name
        dest_root.mkdir(parents=True, exist_ok=True)

        # 4. 先创建所有目录
        file_entries: list[tuple[str, int]] = []
        for item in raw_entries:
            if not isinstance(item, dict):
                continue
            relative = str(item.get("path", "") or "")
            if not relative or relative.startswith("/") or ".." in Path(relative).parts:
                return DownloadResult(False, f"目录清单包含不安全路径: {relative}")
            is_dir = bool(item.get("directory", False))
            dest = dest_root / relative
            if is_dir:
                dest.mkdir(parents=True, exist_ok=True)
            else:
                size = int(item.get("size", 0) or 0)
                if size < 0:
                    return DownloadResult(False, f"文件大小无效: {relative}")
                dest.parent.mkdir(parents=True, exist_ok=True)
                file_entries.append((relative, size))

        # 5. 逐个接收文件内容
        received_bytes = 0
        for relative, size in file_entries:
            dest = dest_root / relative
            with dest.open("wb") as f:
                remaining = size
                while remaining > 0:
                    chunk = await _recv_exact(connection, min(CHUNK_SIZE, remaining))
                    f.write(chunk)
                    remaining -= len(chunk)
                    received_bytes += len(chunk)

        # 6. 读最终结果
        result = await _recv_json_line(connection)
        if not result.get("success"):
            return DownloadResult(False, str(result.get("message", "下载失败")), str(dest_root), len(file_entries), received_bytes)

        return DownloadResult(True, str(result.get("message", "下载完成")), str(dest_root), len(file_entries), received_bytes)
    except asyncio.TimeoutError:
        return DownloadResult(False, "下载超时")
    except (OSError, json.JSONDecodeError, UnicodeDecodeError, ValueError, ConnectionError) as exc:
        return DownloadResult(False, str(exc) or "手机文件服务未连接或返回无法识别")
    except Exception as exc:
        return DownloadResult(False, str(exc) or "下载失败")
    finally:
        if connection is not None:
            try:
                await connection.close()
            except Exception:
                pass
