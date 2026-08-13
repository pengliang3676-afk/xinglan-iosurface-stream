from __future__ import annotations

import asyncio
import json
import struct
import tempfile
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, patch

from xinglan.file_transfer import (
    FileTransferResult,
    build_folder_transfer_header,
    build_transfer_header,
    send_file_to_device,
    send_file_to_devices,
)


class FakeConnection:
    def __init__(self, response: bytes = b'{"success":true,"message":"ok"}\n') -> None:
        self.sent: list[bytes] = []
        self.response = response
        self.closed = False

    async def sendall(self, payload: bytes) -> None:
        self.sent.append(payload)

    async def recv_any(self, _length: int) -> bytes:
        value, self.response = self.response, b""
        return value

    async def close(self) -> None:
        self.closed = True


class FileTransferTests(unittest.TestCase):
    def test_header_matches_existing_xlft_phone_protocol(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "测试图片.jpg"
            path.write_bytes(b"hello")
            header, metadata = build_transfer_header(path, True)

        self.assertEqual(16, len(header))
        self.assertEqual(b"XLFT", header[:4])
        metadata_length, file_size = struct.unpack(">IQ", header[4:])
        self.assertEqual(len(metadata), metadata_length)
        self.assertEqual(5, file_size)
        decoded = json.loads(metadata.decode("utf-8"))
        self.assertEqual("测试图片.jpg", decoded["name"])
        self.assertTrue(decoded["importPhoto"])

    def test_file_is_sent_directly_to_independent_usb_port_6205(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "note.txt"
            path.write_bytes(b"content")
            connection = FakeConnection()
            create = AsyncMock(return_value=connection)
            with patch(
                "xinglan.file_transfer.ServiceConnection.create_using_usbmux",
                new=create,
            ):
                result = asyncio.run(send_file_to_device("device-01", path))

        self.assertTrue(result.success)
        create.assert_awaited_once_with("device-01", 6205, connection_type="USB")
        self.assertEqual(b"XLFT", connection.sent[0][:4])
        self.assertEqual(b"content", connection.sent[-1])
        self.assertTrue(connection.closed)

    def test_folder_manifest_preserves_files_and_empty_directories(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory) / "资料"
            (root / "子目录" / "空目录").mkdir(parents=True)
            (root / "说明.txt").write_bytes(b"hello")
            (root / "子目录" / "零字节.bin").write_bytes(b"")
            header, metadata, files = build_folder_transfer_header(root)

        self.assertEqual(b"XLFD", header[:4])
        metadata_length, total_size = struct.unpack(">IQ", header[4:])
        self.assertEqual(len(metadata), metadata_length)
        self.assertEqual(5, total_size)
        decoded = json.loads(metadata.decode("utf-8"))
        self.assertEqual("资料", decoded["name"])
        self.assertIn(
            {"path": "子目录/空目录", "directory": True}, decoded["entries"]
        )
        self.assertIn(
            {"path": "子目录/零字节.bin", "size": 0}, decoded["entries"]
        )
        self.assertEqual(2, len(files))

    def test_folder_is_sent_over_one_usb_connection(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory) / "folder"
            root.mkdir()
            (root / "a.txt").write_bytes(b"A")
            (root / "b.txt").write_bytes(b"BC")
            connection = FakeConnection()
            create = AsyncMock(return_value=connection)
            with patch(
                "xinglan.file_transfer.ServiceConnection.create_using_usbmux",
                new=create,
            ):
                result = asyncio.run(send_file_to_device("device-01", root, True))

        self.assertTrue(result.success)
        self.assertEqual(b"XLFD", connection.sent[0][:4])
        self.assertEqual([b"A", b"BC"], connection.sent[-2:])
        self.assertTrue(connection.closed)

    def test_multi_device_transfer_deduplicates_targets(self) -> None:
        sender = AsyncMock(return_value=FileTransferResult(True, "ok"))
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "file.bin"
            path.write_bytes(b"data")
            with patch("xinglan.file_transfer._send_prepared_to_device", new=sender):
                result = asyncio.run(send_file_to_devices(["a", "b", "a"], path))
        self.assertEqual(["a", "b"], list(result))
        self.assertEqual(2, sender.await_count)

if __name__ == "__main__":
    unittest.main()
