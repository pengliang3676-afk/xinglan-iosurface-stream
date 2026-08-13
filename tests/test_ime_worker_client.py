from __future__ import annotations

import unittest
from unittest import mock

from xinglan.ime_worker_client import ImeWorkerClient


class Root:
    def __init__(self) -> None:
        self.callbacks: list[tuple[int, object]] = []

    def after(self, milliseconds: int, callback: object) -> None:
        self.callbacks.append((milliseconds, callback))


class ImeWorkerClientTests(unittest.TestCase):
    def test_startup_info_disables_windows_busy_cursor_feedback(self) -> None:
        startup_info = ImeWorkerClient._startup_info()

        if startup_info is None:
            self.skipTest("STARTUPINFO is only available on Windows")
        self.assertEqual(0x80, startup_info.dwFlags & 0x80)

    def test_poll_delivers_text_and_keys_on_main_thread(self) -> None:
        root = Root()
        texts: list[str] = []
        keys: list[tuple[int, int, str]] = []
        client = ImeWorkerClient(root, texts.append, lambda *value: keys.append(value))
        client._messages.put((0, ["text", "中文 ABC 123"]))
        client._messages.put((0, ["key", 0x07, 0x2A, "退格"]))

        client._poll()

        self.assertEqual(["中文 ABC 123"], texts)
        self.assertEqual([(0x07, 0x2A, "退格")], keys)
        self.assertEqual(2, len(root.callbacks))

    def test_poll_ignores_messages_from_replaced_worker(self) -> None:
        root = Root()
        texts: list[str] = []
        client = ImeWorkerClient(root, texts.append, lambda *_value: None)
        client._generation = 4
        client._messages.put((3, ["text", "旧输入"] ))
        client._messages.put((4, ["text", "新输入"] ))

        client._poll()

        self.assertEqual(["新输入"], texts)


if __name__ == "__main__":
    unittest.main()
