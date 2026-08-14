from __future__ import annotations

import struct
import socket
import unittest
from unittest.mock import AsyncMock, patch

from xinglan.trollvnc_rfb import (
    RFB_VERSION_3_8,
    RfbConnectionClosedError,
    RfbSecurityError,
    TrollVNCRfbClient,
)


PIXEL_FORMAT = bytes(range(16))


def server_handshake(
    *,
    width: int = 100,
    height: int = 200,
    name: bytes = b"TrollVNC test",
) -> bytes:
    return b"".join(
        (
            RFB_VERSION_3_8,
            b"\x01\x01",  # one security type: None
            b"\x00\x00\x00\x00",  # SecurityResult OK
            struct.pack(">HH16sI", width, height, PIXEL_FORMAT, len(name)),
            name,
        )
    )


class FakeConnection:
    def __init__(
        self,
        incoming: bytes,
        *,
        chunk_size: int | None = None,
        fail_send_number: int | None = None,
    ) -> None:
        self.incoming = bytearray(incoming)
        self.chunk_size = chunk_size
        self.fail_send_number = fail_send_number
        self.sent: list[bytes] = []
        self.send_calls = 0
        self.closed = False
        self.socket = FakeSocket()

    async def recv_any(self, length: int = 4096) -> bytes:
        if not self.incoming:
            return b""
        take = min(length, len(self.incoming))
        if self.chunk_size is not None:
            take = min(take, self.chunk_size)
        result = bytes(self.incoming[:take])
        del self.incoming[:take]
        return result

    async def sendall(self, payload: bytes) -> None:
        self.send_calls += 1
        if self.fail_send_number == self.send_calls:
            raise ConnectionError("fake transport disconnected")
        self.sent.append(payload)

    async def close(self) -> None:
        self.closed = True


class FakeSocket:
    def __init__(self) -> None:
        self.options: list[tuple[int, int, int]] = []

    def setsockopt(self, level: int, option: int, value: int) -> None:
        self.options.append((level, option, value))


async def connect_fake(connection: FakeConnection) -> TrollVNCRfbClient:
    with patch(
        "xinglan.trollvnc_rfb.ServiceConnection.create_using_usbmux",
        new=AsyncMock(return_value=connection),
    ) as create:
        client = await TrollVNCRfbClient.connect("test-udid")
    create.assert_awaited_once_with(
        "test-udid",
        5901,
        connection_type="USB",
    )
    return client


class TrollVNCRfbHandshakeTests(unittest.IsolatedAsyncioTestCase):
    async def test_successful_handshake_reads_server_init_without_video_request(
        self,
    ) -> None:
        connection = FakeConnection(server_handshake())
        client = await connect_fake(connection)

        self.assertTrue(client.is_connected)
        self.assertEqual(100, client.width)
        self.assertEqual(200, client.height)
        self.assertEqual("TrollVNC test", client.name)
        self.assertEqual(PIXEL_FORMAT, client.server_info.pixel_format)
        self.assertEqual(
            [RFB_VERSION_3_8, b"\x01", b"\x01"],
            connection.sent,
        )
        self.assertEqual(
            [(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)],
            connection.socket.options,
        )
        # Message type 3 would be FramebufferUpdateRequest; none is sent.
        self.assertFalse(any(payload[:1] == b"\x03" for payload in connection.sent))
        await client.close()

    async def test_handshake_accepts_one_byte_short_reads(self) -> None:
        connection = FakeConnection(server_handshake(name="丝滑触控".encode()), chunk_size=1)
        client = await connect_fake(connection)
        self.assertEqual("丝滑触控", client.name)
        await client.close()

    async def test_eof_during_server_init_is_reported_and_connection_closed(
        self,
    ) -> None:
        connection = FakeConnection(server_handshake()[:-3], chunk_size=2)
        with patch(
            "xinglan.trollvnc_rfb.ServiceConnection.create_using_usbmux",
            new=AsyncMock(return_value=connection),
        ):
            with self.assertRaisesRegex(
                RfbConnectionClosedError,
                "connection closed",
            ):
                await TrollVNCRfbClient.connect("test-udid")
        self.assertTrue(connection.closed)

    async def test_server_rejection_reason_is_clear(self) -> None:
        reason = b"server disabled"
        connection = FakeConnection(
            RFB_VERSION_3_8 + b"\x00" + struct.pack(">I", len(reason)) + reason
        )
        with patch(
            "xinglan.trollvnc_rfb.ServiceConnection.create_using_usbmux",
            new=AsyncMock(return_value=connection),
        ):
            with self.assertRaisesRegex(RfbSecurityError, "server disabled"):
                await TrollVNCRfbClient.connect("test-udid")
        self.assertTrue(connection.closed)

    async def test_unsupported_auth_lists_security_types(self) -> None:
        connection = FakeConnection(RFB_VERSION_3_8 + b"\x02\x02\x10")
        with patch(
            "xinglan.trollvnc_rfb.ServiceConnection.create_using_usbmux",
            new=AsyncMock(return_value=connection),
        ):
            with self.assertRaisesRegex(RfbSecurityError, "2, 16"):
                await TrollVNCRfbClient.connect("test-udid")
        self.assertTrue(connection.closed)

    async def test_security_none_can_still_be_rejected(self) -> None:
        reason = b"None auth forbidden"
        connection = FakeConnection(
            RFB_VERSION_3_8
            + b"\x01\x01"
            + b"\x00\x00\x00\x01"
            + struct.pack(">I", len(reason))
            + reason
        )
        with patch(
            "xinglan.trollvnc_rfb.ServiceConnection.create_using_usbmux",
            new=AsyncMock(return_value=connection),
        ):
            with self.assertRaisesRegex(RfbSecurityError, "None auth forbidden"):
                await TrollVNCRfbClient.connect("test-udid")
        self.assertTrue(connection.closed)


class TrollVNCRfbPointerTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        self.connection = FakeConnection(server_handshake())
        self.client = await connect_fake(self.connection)
        self.connection.sent.clear()

    async def asyncTearDown(self) -> None:
        await self.client.close()

    async def test_normalized_coordinates_are_rounded_and_clamped(self) -> None:
        self.assertEqual((0, 199), self.client.normalized_to_pixel(-0.5, 1.7))
        self.assertEqual((50, 50), self.client.normalized_to_pixel(0.5, 0.25))
        self.assertEqual((99, 0), self.client.normalized_to_pixel(1.0, 0.0))

        two_pixel_connection = FakeConnection(server_handshake(width=2, height=2))
        two_pixel_client = await connect_fake(two_pixel_connection)
        self.assertEqual((0, 0), two_pixel_client.normalized_to_pixel(0.5, 0.5))
        await two_pixel_client.close()

    async def test_pointer_down_move_up_are_six_byte_big_endian_messages(self) -> None:
        await self.client.pointer_down(0.0, 0.0)
        await self.client.pointer_move(0.5, 0.25)
        await self.client.pointer_up(1.0, 1.0)

        self.assertEqual(
            [
                struct.pack(">BBHH", 5, 1, 0, 0),
                struct.pack(">BBHH", 5, 1, 50, 50),
                struct.pack(">BBHH", 5, 0, 99, 199),
            ],
            self.connection.sent,
        )
        self.assertTrue(all(len(payload) == 6 for payload in self.connection.sent))

    async def test_close_releases_pressed_button_at_last_coordinate(self) -> None:
        await self.client.pointer_down(0.25, 0.5)
        await self.client.pointer_move(0.75, 0.75)
        last_down_or_move = self.connection.sent[-1]

        await self.client.close()

        _, _, x, y = struct.unpack(">BBHH", last_down_or_move)
        self.assertEqual(struct.pack(">BBHH", 5, 0, x, y), self.connection.sent[-1])
        self.assertFalse(self.client.is_connected)
        self.assertTrue(self.connection.closed)

    async def test_send_failure_marks_connection_disconnected(self) -> None:
        # Handshake consumed three sends; fail the first pointer write.
        self.connection.fail_send_number = 4
        with self.assertRaisesRegex(ConnectionError, "disconnected"):
            await self.client.pointer_down(0.5, 0.5)

        self.assertFalse(self.client.is_connected)
        with self.assertRaises(RfbConnectionClosedError):
            await self.client.pointer_move(0.6, 0.6)


if __name__ == "__main__":
    unittest.main()
