from __future__ import annotations

import ctypes
from pathlib import Path
import sys
import unittest
from unittest import mock

from xinglan.native_ime_worker import NativeImeHost, POINT, native_host_target


class NativeImeWorkerTests(unittest.TestCase):
    def test_native_host_follows_clicked_phone_client_point(self) -> None:
        class ClientToScreen:
            argtypes: object = None

            def __call__(self, _hwnd: object, point_pointer: object) -> int:
                point = ctypes.cast(point_pointer, ctypes.POINTER(POINT)).contents
                point.x += 300
                point.y += 80
                return 1

        class User32:
            def __init__(self) -> None:
                self.ClientToScreen = ClientToScreen()

        self.assertEqual(
            (720, 260),
            native_host_target(User32(), 123456, 420, 180, 12, 34),
        )

    def test_no_owner_uses_original_screen_fallback(self) -> None:
        self.assertEqual(
            (-1200, 42),
            native_host_target(object(), 0, 0, 0, -1200, 42),
        )

    def test_production_entry_uses_native_worker_on_windows(self) -> None:
        source = (Path(__file__).resolve().parents[1] / "xinglan" / "ime_worker.py").read_text(
            encoding="utf-8"
        )
        self.assertIn('if sys.platform == "win32":', source)
        self.assertIn("args.anchor_x", source)
        self.assertIn("args.anchor_y", source)

    def test_native_worker_owns_edit_and_does_not_move_ime_windows(self) -> None:
        source = (
            Path(__file__).resolve().parents[1] / "xinglan" / "native_ime_worker.py"
        ).read_text(encoding="utf-8")
        self.assertIn('"EDIT"', source)
        self.assertIn("WM_IME_STARTCOMPOSITION", source)
        self.assertIn("WM_IME_ENDCOMPOSITION", source)
        self.assertIn("HideCaret", source)
        self.assertNotIn("SetWinEventHook", source)
        self.assertNotIn("EnumWindows", source)

    @unittest.skipUnless(sys.platform == "win32", "requires Win32 EDIT")
    def test_real_native_edit_emits_and_clears_committed_text(self) -> None:
        host = NativeImeHost(800, 700, 0)
        try:
            host._create_windows()
            with mock.patch("xinglan.native_ime_worker.emit") as emit:
                ctypes.windll.user32.SendMessageW(
                    host.edit_hwnd, 0x0102, ord("A"), 0
                )
                host.flush_committed_text()
            emit.assert_called_once_with("text", "A")
            self.assertEqual(
                0, ctypes.windll.user32.GetWindowTextLengthW(host.edit_hwnd)
            )
        finally:
            if host.host_hwnd:
                ctypes.windll.user32.DestroyWindow(host.host_hwnd)
            host.close()


if __name__ == "__main__":
    unittest.main()
