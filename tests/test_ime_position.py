from __future__ import annotations

import unittest

from xinglan.ime_position import place_ime_caret


class FakeTk:
    def __init__(self) -> None:
        self.calls: list[tuple[object, ...]] = []

    def call(self, *args: object) -> None:
        self.calls.append(args)


class FakeWidget:
    _w = ".phone"

    def __init__(self) -> None:
        self.tk = FakeTk()

    @staticmethod
    def winfo_width() -> int:
        return 300

    @staticmethod
    def winfo_height() -> int:
        return 600


class ImePositionTests(unittest.TestCase):
    def test_candidate_anchor_is_relative_to_clicked_phone_canvas(self) -> None:
        widget = FakeWidget()
        self.assertTrue(place_ime_caret(widget, 120, 88, height=26))
        self.assertEqual(
            ("tk", "caret", ".phone", "-x", 120, "-y", 88, "-height", 26),
            widget.tk.calls[-1],
        )

    def test_candidate_anchor_is_clamped_inside_canvas(self) -> None:
        widget = FakeWidget()
        self.assertTrue(place_ime_caret(widget, 999, -4))
        self.assertEqual(299, widget.tk.calls[-1][4])
        self.assertEqual(0, widget.tk.calls[-1][6])


if __name__ == "__main__":
    unittest.main()
