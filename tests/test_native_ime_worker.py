from __future__ import annotations

import ctypes
from pathlib import Path
import sys
import unittest
from unittest import mock

from xinglan.native_ime_worker import NativeImeHost, RECT, native_host_target


class NativeImeWorkerTests(unittest.TestCase):
    def test_native_host_lives_in_owner_lower_right_area(self) -> None:
        owner = RECT(100, 50, 1500, 900)
        self.assertEqual((1110, 750), native_host_target(owner, 12, 34))

    def test_small_owner_clamps_host_inside_owner(self) -> None:
        owner = RECT(100, 50, 300, 160)
        self.assertEqual((120, 90), native_host_target(owner, 12, 34))

    def test_no_owner_uses_original_screen_fallback(self) -> None:
        self.assertEqual((-1200, 42), native_host_target(None, -1200, 42))

    def test_production_entry_uses_native_worker_on_windows(self) -> None:
        source = (Path(__file__).resolve().parents[1] / "xinglan" / "ime_worker.py").read_text(
            encoding="utf-8"
        )
        self.assertIn('if sys.platform == "win32":', source)
        self.assertIn("run_native_ime_worker(args.x, args.y, args.owner_hwnd)", source)

    def test_native_worker_owns_edit_and_does_not_move_ime_windows(self) -> None:
        source = (
            Path(__file__).resolve().parents[1] / "xinglan" / "native_ime_worker.py"
        ).read_text(encoding="utf-8")
        self.assertIn('"EDIT"', source)
        self.assertIn("WM_IME_STARTCOMPOSITION", source)
        self.assertIn("WM_IME_ENDCOMPOSITION", source)
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
