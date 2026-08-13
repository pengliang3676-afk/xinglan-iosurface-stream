from __future__ import annotations

import sys
import unittest
from pathlib import Path

PROJECT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT))

from xinglan.video_decoder import create_h264_decoder


class PerformanceTests(unittest.TestCase):
    def test_software_decoder_is_always_available(self) -> None:
        decoder = create_h264_decoder()
        self.assertEqual("软件", decoder.name)

if __name__ == "__main__":
    unittest.main()
