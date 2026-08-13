from __future__ import annotations

import unittest
from unittest import mock
import sys

from xinglan.ime_worker_client import ImeWorkerClient


class Root:
    def __init__(self) -> None:
        self.callbacks: list[tuple[int, object]] = []

    def after(self, milliseconds: int, callback: object) -> None:
        self.callbacks.append((milliseconds, callback))


class ImeWorkerClientTests(unittest.TestCase):
    def test_frozen_worker_restarts_same_executable_with_private_mode(self) -> None:
        with mock.patch.object(sys, "frozen", True, create=True), mock.patch(
            "xinglan.ime_worker_client.Path.is_file", return_value=False
        ):
            command = ImeWorkerClient._worker_command(0, 25)
        self.assertEqual(sys.executable, command[0])
        self.assertEqual("--ime-worker", command[1])
        self.assertEqual(["--x", "1", "--y", "25"], command[2:])

    def test_frozen_worker_prefers_bundled_console_helper(self) -> None:
        with mock.patch.object(sys, "frozen", True, create=True), mock.patch(
            "xinglan.ime_worker_client.Path.is_file", return_value=True
        ):
            command = ImeWorkerClient._worker_command(12, 34)
        self.assertTrue(command[0].endswith("星澜输入.exe"))
        self.assertEqual(["--x", "12", "--y", "34"], command[1:])

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

    def test_deactivate_never_waits_for_worker_on_ui_thread(self) -> None:
        root = Root()
        client = ImeWorkerClient(root, lambda _text: None, lambda *_value: None)
        process = mock.Mock()
        process.poll.return_value = None
        client._process = process

        with mock.patch("xinglan.ime_worker_client.threading.Thread") as thread:
            client.deactivate()

        process.terminate.assert_called_once_with()
        process.wait.assert_not_called()
        thread.assert_called_once_with(
            target=client._reap_process,
            args=(process,),
            name="xinglan-ime-reaper",
            daemon=True,
        )
        thread.return_value.start.assert_called_once_with()


if __name__ == "__main__":
    unittest.main()
