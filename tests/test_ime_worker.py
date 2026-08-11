from __future__ import annotations

import io
import json
import sys
import unittest

from xinglan.ime_worker import IME_WINDOW_ALPHA, emit


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


if __name__ == "__main__":
    unittest.main()
