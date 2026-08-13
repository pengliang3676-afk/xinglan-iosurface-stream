from __future__ import annotations

import asyncio
import contextlib
import socket
from typing import Any

from .bootstrap import configure_dependencies


configure_dependencies()

from pymobiledevice3.exceptions import (  # noqa: E402
    DeviceNotFoundError,
    NoDeviceConnectedError,
)
from pymobiledevice3.usbmux import select_device  # noqa: E402


class UsbServiceConnection:
    """Minimal usbmux byte stream used by XLStream.

    The application only needs to open a phone port and exchange bytes.  This
    deliberately avoids pymobiledevice3's CLI/shell helpers, which otherwise
    pull a large interactive Python environment into the Windows EXE.
    """

    def __init__(self, sock: socket.socket) -> None:
        # asyncio.open_connection(sock=...) expects a non-blocking socket.
        # Enabling keepalive also lets Windows notice a dead USB tunnel instead
        # of leaving a stale group session around indefinitely.
        sock.setblocking(False)
        with contextlib.suppress(OSError):
            sock.setsockopt(socket.SOL_SOCKET, socket.SO_KEEPALIVE, 1)
        self.socket: socket.socket | None = sock
        self.reader: asyncio.StreamReader | None = None
        self.writer: asyncio.StreamWriter | None = None
        self._start_lock = asyncio.Lock()

    @classmethod
    async def create_using_usbmux(
        cls,
        udid: str | None,
        port: int,
        connection_type: str | None = None,
        usbmux_address: str | None = None,
    ) -> "UsbServiceConnection":
        device = await select_device(
            udid,
            connection_type=connection_type,
            usbmux_address=usbmux_address,
        )
        if device is None:
            if udid:
                raise DeviceNotFoundError(udid)
            raise NoDeviceConnectedError()
        sock = await device.connect(port, usbmux_address=usbmux_address)
        return cls(sock)

    async def _ensure_started(self) -> None:
        if self.reader is not None and self.writer is not None:
            return
        async with self._start_lock:
            if self.reader is not None and self.writer is not None:
                return
            if self.socket is None:
                raise ConnectionError("USB连接已关闭")
            self.reader, self.writer = await asyncio.open_connection(sock=self.socket)

    async def recv_any(self, length: int = 4096) -> bytes:
        await self._ensure_started()
        assert self.reader is not None
        return await self.reader.read(length)

    async def sendall(self, payload: bytes) -> None:
        await self._ensure_started()
        assert self.writer is not None
        self.writer.write(payload)
        await self.writer.drain()

    async def close(self) -> None:
        writer = self.writer
        sock = self.socket
        self.reader = None
        self.writer = None
        self.socket = None
        if writer is not None:
            with contextlib.suppress(Exception):
                writer.close()
            with contextlib.suppress(Exception):
                await asyncio.wait_for(writer.wait_closed(), timeout=1.0)
        if sock is not None:
            with contextlib.suppress(Exception):
                sock.close()


# Preserve the existing import name so the control, video and file-transfer
# modules need no protocol changes.
ServiceConnection: Any = UsbServiceConnection
