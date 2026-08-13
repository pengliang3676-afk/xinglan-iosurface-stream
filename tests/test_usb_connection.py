from __future__ import annotations

import asyncio
import socket
import unittest

from xinglan.usb_connection import UsbServiceConnection


class UsbServiceConnectionTests(unittest.IsolatedAsyncioTestCase):
    async def test_socket_is_non_blocking_and_exchanges_bytes(self) -> None:
        left, right = socket.socketpair()
        connection = UsbServiceConnection(left)
        self.assertFalse(left.getblocking())

        await connection.sendall(b"ping")
        loop = asyncio.get_running_loop()
        self.assertEqual(await loop.sock_recv(right, 4), b"ping")

        await loop.sock_sendall(right, b"pong")
        self.assertEqual(await connection.recv_any(4), b"pong")

        await connection.close()
        right.close()

    async def test_close_before_first_io_is_safe(self) -> None:
        left, right = socket.socketpair()
        connection = UsbServiceConnection(left)
        await connection.close()
        self.assertIsNone(connection.socket)
        right.close()
