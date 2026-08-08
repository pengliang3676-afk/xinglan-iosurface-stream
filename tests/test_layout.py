from __future__ import annotations

import sys
import unittest
from pathlib import Path

PROJECT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT))

from app import MASTER_VIEW_SIZE, WALL_COLUMNS, WALL_TILE_SIZE


class LayoutTests(unittest.TestCase):
    def test_wall_matches_previous_five_column_layout(self) -> None:
        self.assertEqual(5, WALL_COLUMNS)

    def test_wall_tiles_are_compact(self) -> None:
        self.assertEqual((160, 284), WALL_TILE_SIZE)

    def test_master_view_uses_native_stream_size(self) -> None:
        self.assertEqual((360, 640), MASTER_VIEW_SIZE)


if __name__ == "__main__":
    unittest.main()
