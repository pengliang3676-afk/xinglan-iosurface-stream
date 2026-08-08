from __future__ import annotations

import sys
import tempfile
import unittest
from unittest.mock import patch
from pathlib import Path

PROJECT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT))

from xinglan.diagnostics import StabilityMonitor
from xinglan.video_decoder import create_h264_decoder, probe_hardware_backend


class PerformanceTests(unittest.TestCase):
    def test_software_decoder_is_always_available(self) -> None:
        decoder = create_h264_decoder("software")
        self.assertFalse(decoder.hardware)
        self.assertEqual("软件", decoder.name)

    def test_software_decoder_is_the_stable_default(self) -> None:
        with patch.dict("os.environ", {}, clear=True):
            self.assertEqual("software", probe_hardware_backend(PROJECT))

    def test_stability_report_contains_core_metrics(self) -> None:
        monitor = StabilityMonitor(0)
        monitor.record(
            devices=10,
            fps=119.0,
            cpu=5.0,
            memory_mb=220.0,
            reconnects=0,
            decode_errors=0,
            dropped_frames=0,
            max_frame_age_ms=80.0,
            hardware_decoders=10,
        )
        with tempfile.TemporaryDirectory() as directory:
            report = monitor.write_report(Path(directory), "d3d11va")
            text = report.read_text(encoding="utf-8")
        self.assertIn("判定：通过", text)
        self.assertIn("硬件解码：最低 10 台", text)
        self.assertIn("总帧率：平均 119.0", text)

    def test_stability_report_rejects_continuous_memory_growth(self) -> None:
        monitor = StabilityMonitor(60)
        monitor.started_at -= 600
        for memory_mb in (244.0, 412.0):
            monitor.record(
                devices=10,
                fps=118.6,
                cpu=1.3,
                memory_mb=memory_mb,
                reconnects=0,
                decode_errors=0,
                dropped_frames=0,
                max_frame_age_ms=94.0,
                hardware_decoders=10,
            )
        with tempfile.TemporaryDirectory() as directory:
            report = monitor.write_report(Path(directory), "d3d11va")
            text = report.read_text(encoding="utf-8")
        self.assertIn("判定：需要检查", text)
        self.assertIn("平均每分钟", text)


if __name__ == "__main__":
    unittest.main()
