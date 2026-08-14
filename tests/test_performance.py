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

    def test_e5_profile_uses_fast_tiles_but_keeps_master_high_quality(self) -> None:
        app_source = (PROJECT / "app.py").read_text(encoding="utf-8")
        build_source = (PROJECT / "tools" / "build_windows_exe.ps1").read_text(
            encoding="utf-8-sig"
        )
        self.assertIn('PROJECT_DIR / "E5_RENDER_PROFILE"', app_source)
        self.assertIn("Image.Resampling.BILINEAR", app_source)
        self.assertEqual(1, app_source.count("target_size, TILE_RESAMPLING"))
        self.assertEqual(
            1, app_source.count("target_size, Image.Resampling.LANCZOS")
        )
        self.assertIn("[switch]$E5Optimized", build_source)
        self.assertIn('"星澜_双路E5优化版"', build_source)
        self.assertIn('"星澜_双路E5"', build_source)

if __name__ == "__main__":
    unittest.main()
