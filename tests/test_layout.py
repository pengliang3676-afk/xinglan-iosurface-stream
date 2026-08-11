from __future__ import annotations

import sys
import unittest
from pathlib import Path

PROJECT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT))

from app import (
    MASTER_VIEW_SIZE,
    PHONE_HEAD_HEIGHT,
    RIGHT_PANEL_WIDTH,
    SIDE_RAIL_WIDTH,
    TOP_BAR_HEIGHT,
    WALL_COLUMNS,
    WALL_GAP,
    WALL_ROWS,
)


class LayoutTests(unittest.TestCase):
    def test_wall_matches_previous_five_column_layout(self) -> None:
        self.assertEqual(5, WALL_COLUMNS)

    def test_wall_has_two_rows(self) -> None:
        self.assertEqual(2, WALL_ROWS)

    def test_master_view_fallback_is_portrait(self) -> None:
        width, height = MASTER_VIEW_SIZE
        self.assertLess(width, height)
        self.assertGreater(width / height, 0.5)
        self.assertLess(width / height, 0.65)

    def test_layout_matches_starlan_card_structure(self) -> None:
        self.assertEqual(64, TOP_BAR_HEIGHT)
        self.assertEqual(20, PHONE_HEAD_HEIGHT)
        self.assertEqual(44, SIDE_RAIL_WIDTH)
        self.assertEqual(3, WALL_GAP)
        self.assertEqual(360, RIGHT_PANEL_WIDTH)


if __name__ == "__main__":
    unittest.main()
