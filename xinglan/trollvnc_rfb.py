from __future__ import annotations

import asyncio
import contextlib
import math
import socket
import struct
from dataclasses import dataclass
from typing import Any

from .usb_connection import ServiceConnection


RFB_VERSION_3_8 = b"RFB 003.008\n"
RFB_SECURITY_NONE = 1
RFB_POINTER_EVENT = 5
RFB_LEFT_BUTTON = 1
DEFAULT_TROLLVNC_PORT = 5901

_SERVER_INIT = struct.Struct(">HH16sI")
_POINTER_EVENT = struct.Struct(">BBHH")
_MAX_SERVER_NAME_BYTES = 1024 * 1024


class RfbError(Exception):
    """Base error for the TrollVNC control-only connection."""


class RfbProtocolError(RfbError):
    """The peer sent an invalid or unsupported RFB message."""


class RfbSecurityError(RfbError):
    """TrollVNC rejected the connection or requires unsupported auth."""


class RfbConnectionClosedError(ConnectionError, RfbError):
    """The RFB byte stream ended before an operation completed."""


@dataclass(frozen=True)
class RfbServerInfo:
    width: int
    height: int
    name: str
    pixel_format: bytes


async def _read_exact(connection: Any, length: int) -> bytes:
    """Read exactly *length* bytes from the usbmux byte stream."""

    if length < 0:
        raise ValueError("length must not be negative")
    chunks: list[bytes] = []
    remaining = length
    while remaining:
        chunk = await connection.recv_any(remaining)
        if not chunk:
            received = length - remaining
            raise RfbConnectionClosedError(
                f"TrollVNC connection closed after {received} of {length} bytes"
            )
        if len(chunk) > remaining:
            raise RfbProtocolError("RFB stream returned more bytes than requested")
        chunks.append(chunk)
        remaining -= len(chunk)
    return b"".join(chunks)


async def _read_reason(connection: Any) -> str:
    reason_length = struct.unpack(">I", await _read_exact(connection, 4))[0]
    if reason_length > _MAX_SERVER_NAME_BYTES:
        raise RfbProtocolError(
            f"RFB failure reason is unreasonably large: {reason_length} bytes"
        )
    reason = await _read_exact(connection, reason_length)
    return reason.decode("utf-8", errors="replace")


class TrollVNCRfbClient:
    """Small RFB 3.8 client that uses TrollVNC only as a touch endpoint.

    The client deliberately never sends ``FramebufferUpdateRequest``.  It
    completes just enough of the RFB handshake to send six-byte PointerEvent
    messages over the existing usbmux connection.
    """

    def __init__(self, connection: Any) -> None:
        self._connection: Any | None = connection
        self._write_lock = asyncio.Lock()
        self._connected = False
        self._closing = False
        self._button_mask = 0
        self._last_pixel: tuple[int, int] | None = None
        self.server_info: RfbServerInfo | None = None

    @classmethod
    async def connect(
        cls,
        udid: str,
        port: int = DEFAULT_TROLLVNC_PORT,
        *,
        timeout: float = 4.0,
    ) -> "TrollVNCRfbClient":
        """Open ``udid:port`` over USB and complete an RFB 3.8 handshake."""

        connection = await asyncio.wait_for(
            ServiceConnection.create_using_usbmux(
                udid,
                port,
                connection_type="USB",
            ),
            timeout=timeout,
        )
        # Pointer packets are only six bytes.  Best-effort TCP_NODELAY keeps
        # Nagle buffering from adding visible drag latency to the USB tunnel.
        channel_socket = getattr(connection, "socket", None)
        if channel_socket is not None:
            with contextlib.suppress(AttributeError, OSError):
                channel_socket.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
        client = cls(connection)
        try:
            await asyncio.wait_for(client._handshake(), timeout=timeout)
        except BaseException:
            client._connected = False
            client._connection = None
            with contextlib.suppress(Exception):
                await connection.close()
            raise
        return client

    @property
    def is_connected(self) -> bool:
        return self._connected and self._connection is not None

    @property
    def width(self) -> int:
        if self.server_info is None:
            raise RfbConnectionClosedError("RFB handshake has not completed")
        return self.server_info.width

    @property
    def height(self) -> int:
        if self.server_info is None:
            raise RfbConnectionClosedError("RFB handshake has not completed")
        return self.server_info.height

    @property
    def name(self) -> str:
        if self.server_info is None:
            raise RfbConnectionClosedError("RFB handshake has not completed")
        return self.server_info.name

    async def _handshake(self) -> None:
        connection = self._require_connection()

        version = await _read_exact(connection, len(RFB_VERSION_3_8))
        if version != RFB_VERSION_3_8:
            printable = version.decode("ascii", errors="replace").rstrip()
            raise RfbProtocolError(
                f"TrollVNC must support RFB 3.8; server announced {printable!r}"
            )
        await connection.sendall(RFB_VERSION_3_8)

        security_count = (await _read_exact(connection, 1))[0]
        if security_count == 0:
            reason = await _read_reason(connection)
            raise RfbSecurityError(f"TrollVNC rejected the connection: {reason}")

        security_types = await _read_exact(connection, security_count)
        if RFB_SECURITY_NONE not in security_types:
            offered = ", ".join(str(value) for value in security_types)
            raise RfbSecurityError(
                "TrollVNC requires authentication that this control-only "
                f"prototype does not support (security types: {offered})"
            )
        await connection.sendall(bytes((RFB_SECURITY_NONE,)))

        security_result = struct.unpack(">I", await _read_exact(connection, 4))[0]
        if security_result != 0:
            reason = await _read_reason(connection)
            raise RfbSecurityError(
                f"TrollVNC rejected SecurityType None: {reason or security_result}"
            )

        # Shared=1 avoids disconnecting another diagnostic/browser client.
        await connection.sendall(b"\x01")
        width, height, pixel_format, name_length = _SERVER_INIT.unpack(
            await _read_exact(connection, _SERVER_INIT.size)
        )
        if width == 0 or height == 0:
            raise RfbProtocolError(
                f"TrollVNC reported an invalid framebuffer size: {width}x{height}"
            )
        if name_length > _MAX_SERVER_NAME_BYTES:
            raise RfbProtocolError(
                f"TrollVNC server name is unreasonably large: {name_length} bytes"
            )
        name_bytes = await _read_exact(connection, name_length)
        self.server_info = RfbServerInfo(
            width=width,
            height=height,
            name=name_bytes.decode("utf-8", errors="replace"),
            pixel_format=pixel_format,
        )
        self._connected = True

    def normalized_to_pixel(self, x: float, y: float) -> tuple[int, int]:
        """Clamp normalized coordinates and map them to the framebuffer."""

        if not math.isfinite(x) or not math.isfinite(y):
            raise ValueError("touch coordinates must be finite")
        x = min(1.0, max(0.0, float(x)))
        y = min(1.0, max(0.0, float(y)))
        # Nearest-pixel mapping keeps both normalized endpoints in bounds.
        pixel_x = round(x * (self.width - 1))
        pixel_y = round(y * (self.height - 1))
        return pixel_x, pixel_y

    async def send_pointer_normalized(
        self,
        x: float,
        y: float,
        button_mask: int | None = None,
    ) -> None:
        """Send one normalized RFB PointerEvent.

        When *button_mask* is omitted the current mask is retained, which is
        convenient for MOVE events between ``pointer_down`` and ``pointer_up``.
        """

        pixel_x, pixel_y = self.normalized_to_pixel(x, y)
        mask = self._button_mask if button_mask is None else button_mask
        if not 0 <= mask <= 0xFF:
            raise ValueError("button_mask must fit in one byte")

        async with self._write_lock:
            await self._send_pointer_locked(pixel_x, pixel_y, mask)

    async def send_pointer(
        self,
        x: float,
        y: float,
        button_mask: int | None = None,
    ) -> None:
        """Compatibility shorthand for :meth:`send_pointer_normalized`."""

        await self.send_pointer_normalized(x, y, button_mask)

    async def pointer_down(self, x: float, y: float) -> None:
        await self.send_pointer_normalized(x, y, RFB_LEFT_BUTTON)

    async def pointer_move(self, x: float, y: float) -> None:
        await self.send_pointer_normalized(x, y)

    async def pointer_up(self, x: float, y: float) -> None:
        await self.send_pointer_normalized(x, y, 0)

    async def _send_pointer_locked(
        self,
        pixel_x: int,
        pixel_y: int,
        button_mask: int,
    ) -> None:
        if self._closing:
            raise RfbConnectionClosedError("TrollVNC connection is closing")
        connection = self._require_connected()
        payload = _POINTER_EVENT.pack(
            RFB_POINTER_EVENT,
            button_mask,
            pixel_x,
            pixel_y,
        )
        try:
            await connection.sendall(payload)
        except BaseException:
            # A later call must never report a failed transport as connected.
            self._connected = False
            raise
        self._button_mask = button_mask
        self._last_pixel = (pixel_x, pixel_y)

    async def close(self) -> None:
        """Best-effort release any pressed button, then close the USB stream."""

        connection: Any | None = None
        async with self._write_lock:
            if self._closing:
                return
            self._closing = True
            connection = self._connection
            if (
                connection is not None
                and self._connected
                and self._button_mask
                and self._last_pixel is not None
            ):
                pixel_x, pixel_y = self._last_pixel
                payload = _POINTER_EVENT.pack(
                    RFB_POINTER_EVENT,
                    0,
                    pixel_x,
                    pixel_y,
                )
                with contextlib.suppress(Exception):
                    await connection.sendall(payload)
            self._button_mask = 0
            self._connected = False
            self._connection = None

        if connection is not None:
            with contextlib.suppress(Exception):
                await connection.close()

    async def __aenter__(self) -> "TrollVNCRfbClient":
        if not self.is_connected:
            raise RfbConnectionClosedError("TrollVNC connection is not active")
        return self

    async def __aexit__(self, *_exc_info: object) -> None:
        await self.close()

    def _require_connection(self) -> Any:
        if self._connection is None:
            raise RfbConnectionClosedError("TrollVNC connection is closed")
        return self._connection

    def _require_connected(self) -> Any:
        if not self.is_connected:
            raise RfbConnectionClosedError("TrollVNC connection is not active")
        return self._require_connection()
