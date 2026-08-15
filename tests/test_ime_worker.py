from __future__ import annotations

import ctypes
import io
import json
from pathlib import Path
import sys
import unittest

from xinglan.ime_worker import (
    IME_WINDOW_ALPHA,
    POINT,
    RECT,
    candidate_target_in_owner,
    create_native_caret,
    emit,
    is_stuck_top_left_candidate,
    resolve_screen_anchor,
)


class ImeWorkerTests(unittest.TestCase):
    def test_helper_window_is_fully_transparent(self) -> None:
        self.assertEqual(0.0, IME_WINDOW_ALPHA)

    def test_chinese_wire_message_is_ascii_safe_and_round_trips(self) -> None:
        stream = io.StringIO()
        original = sys.stdout
        try:
            sys.stdout = stream
            emit("text", "中文 ABC 123")
        finally:
            sys.stdout = original

        wire = stream.getvalue()
        self.assertTrue(wire.isascii())
        self.assertEqual(["text", "中文 ABC 123"], json.loads(wire))

    def test_helper_does_not_abandon_keyboard_focus_after_idle_timeout(self) -> None:
        source = (Path(__file__).resolve().parents[1] / "xinglan" / "ime_worker.py").read_text(
            encoding="utf-8"
        )
        self.assertNotIn("IDLE_EXIT_MS", source)
        self.assertNotIn("root.after(IDLE", source)

    def test_helper_sets_native_position_and_ime_caret_anchor(self) -> None:
        source = (Path(__file__).resolve().parents[1] / "xinglan" / "ime_worker.py").read_text(
            encoding="utf-8"
        )
        self.assertIn("user32.SetWindowPos", source)
        self.assertIn("place_ime_caret(entry, 0, 0, height=24)", source)
        self.assertIn("ImmSetCompositionWindow", source)
        self.assertIn("ImmSetCandidateWindow", source)
        self.assertIn("create_native_caret", source)

    def test_anchor_follows_owner_client_point(self) -> None:
        class ClientToScreen:
            argtypes: object = None

            def __call__(self, _hwnd: object, point_pointer: object) -> int:
                point = ctypes.cast(
                    point_pointer,
                    ctypes.POINTER(POINT),
                ).contents
                point.x += 300
                point.y += 80
                return 1

        class User32:
            def __init__(self) -> None:
                self.ClientToScreen = ClientToScreen()

        self.assertEqual(
            (720, 260),
            resolve_screen_anchor(User32(), 440, 218, 123456, 420, 180),
        )

    def test_tsf_anchor_creates_caret_on_actual_focused_window(self) -> None:
        class Function:
            argtypes: object = None
            restype: object = None

            def __init__(self, result: object = 1) -> None:
                self.result = result
                self.calls: list[tuple[object, ...]] = []

            def __call__(self, *args: object) -> object:
                self.calls.append(args)
                return self.result

        class User32:
            def __init__(self) -> None:
                self.GetFocus = Function(9988)
                self.CreateCaret = Function(1)
                self.SetCaretPos = Function(1)
                self.ShowCaret = Function(1)
                self.DestroyCaret = Function(1)

        user32 = User32()
        self.assertTrue(create_native_caret(user32))
        self.assertEqual((0, 0), user32.SetCaretPos.calls[-1])
        self.assertEqual((2, 24), user32.CreateCaret.calls[-1][-2:])

    def test_only_compact_top_left_windows_match_candidate_fallback(self) -> None:
        self.assertTrue(is_stuck_top_left_candidate(RECT(20, 60, 365, 125)))
        self.assertFalse(is_stuck_top_left_candidate(RECT(20, 30, 1900, 1050)))
        self.assertFalse(is_stuck_top_left_candidate(RECT(600, 60, 945, 125)))
        self.assertFalse(is_stuck_top_left_candidate(RECT(20, 60, 60, 90)))

    def test_candidate_target_follows_owner_window_bottom_right(self) -> None:
        owner = RECT(100, 50, 1500, 900)
        candidate = RECT(0, 0, 345, 65)
        self.assertEqual((1135, 775), candidate_target_in_owner(owner, candidate))

    def test_candidate_positioning_is_event_driven_with_polling_only_fallback(self) -> None:
        source = (Path(__file__).resolve().parents[1] / "xinglan" / "ime_worker.py").read_text(
            encoding="utf-8"
        )
        self.assertIn("SetWinEventHook", source)
        self.assertIn("EVENT_OBJECT_LOCATIONCHANGE", source)
        self.assertIn("self.pinned_handles", source)
        self.assertIn("ANCHOR_POLL_MS = 250", source)
        self.assertLess(
            source.index("candidate_pinner.start()", source.index("def run_worker")),
            source.index("root.after(20, force_focus)"),
        )


if __name__ == "__main__":
    unittest.main()
