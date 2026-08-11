from __future__ import annotations

import sys
import unittest
from pathlib import Path

PROJECT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT))

from xinglan.control_protocol import KeyModifier
from xinglan.keyboard_input import HIDKeystroke, map_keypress


class KeyboardInputTests(unittest.TestCase):
    def test_lowercase_letters_feed_phone_pinyin_without_shift(self) -> None:
        self.assertEqual(HIDKeystroke(0x07, 0x04), map_keypress("a", "a"))
        self.assertEqual(HIDKeystroke(0x07, 0x1D), map_keypress("z", "z"))

    def test_uppercase_and_symbols_use_hid_shift(self) -> None:
        self.assertEqual(
            HIDKeystroke(0x07, 0x04, int(KeyModifier.SHIFT)),
            map_keypress("A", "A"),
        )
        self.assertEqual(
            HIDKeystroke(0x07, 0x1F, int(KeyModifier.SHIFT)),
            map_keypress("at", "@"),
        )

    def test_digits_punctuation_and_navigation(self) -> None:
        self.assertEqual(HIDKeystroke(0x07, 0x27), map_keypress("0", "0"))
        self.assertEqual(HIDKeystroke(0x07, 0x2C), map_keypress("space", " "))
        self.assertEqual(HIDKeystroke(0x07, 0x2A), map_keypress("BackSpace", ""))
        self.assertEqual(HIDKeystroke(0x07, 0x50), map_keypress("Left", ""))
        self.assertEqual(HIDKeystroke(0x07, 0x45), map_keypress("F12", ""))

    def test_non_ascii_pc_ime_commit_is_not_forwarded(self) -> None:
        self.assertIsNone(map_keypress("??", "中文"))
        self.assertIsNone(map_keypress("Shift_L", ""))


if __name__ == "__main__":
    unittest.main()
