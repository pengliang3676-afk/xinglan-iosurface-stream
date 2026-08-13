from __future__ import annotations

import sys
import unittest
from pathlib import Path

PROJECT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT))

from xinglan.bootstrap import configure_dependencies

configure_dependencies()

from PIL import Image

from xinglan.protocol import (
    parse_frame_size,
    parse_video_header,
    parse_video_header_v3,
    parse_video_packet_header,
    touch_message,
)
from xinglan.diagnostics import ProcessLoadSampler, working_set_mb
from xinglan.session import DeviceSession, LatestFrame, prepare_xlv3_image


class ProtocolTests(unittest.TestCase):
    def test_video_header(self) -> None:
        header = parse_video_header(b"ZXH2\x01h\x02\x80\x00\x0c\x00\x00")
        self.assertEqual((360, 640, 12), (header.width, header.height, header.fps))
        self.assertEqual("ZXH2", header.protocol)

    def test_video_header_v3(self) -> None:
        header = parse_video_header_v3(
            b"XLV3\x01h\x02\x80\x00\x0c\x01\x00\x00\x00\x00\x00"
        )
        self.assertEqual(("XLV3", 360, 640, 12), (header.protocol, header.width, header.height, header.fps))

    def test_video_packet_header_v3(self) -> None:
        packet = parse_video_packet_header(
            b"\x01\x01\x00\x00\x00\x00\x04\x00\x00\x00\x00\x07\x00\x00\x00\x09"
        )
        self.assertEqual((1, 1, 1024, 7, 9), (
            packet.packet_type, packet.flags, packet.payload_length, packet.sequence, packet.timestamp_ms
        ))

    def test_frame_size_validation(self) -> None:
        self.assertEqual(1024, parse_frame_size(b"\x00\x00\x04\x00"))
        with self.assertRaises(ValueError):
            parse_frame_size(b"\x00\x00\x00\x00")

    def test_touch_message(self) -> None:
        self.assertEqual(b"1011010375006670\r\n", touch_message(1, 0.5, 0.5))

    def test_touch_coordinates_are_clamped(self) -> None:
        self.assertEqual(b"1010010000000000\r\n", touch_message(0, -5, -2))
        self.assertEqual(b"1012010749013330\r\n", touch_message(2, 8, 9))

    def test_latest_frame_overwrites_without_queue(self) -> None:
        slot = LatestFrame()
        first = Image.new("RGB", (2, 2), "red")
        second = Image.new("RGB", (2, 2), "blue")
        slot.publish(first)
        slot.publish(second)
        sequence, _, image = slot.snapshot()
        self.assertEqual(2, sequence)
        self.assertIs(second, image)

    def test_stopping_idle_session_releases_latest_frame(self) -> None:
        session = DeviceSession("test-device")
        session.latest.publish(Image.new("RGB", (360, 640), "blue"))
        session.stop()
        _, received_at, image = session.latest.snapshot()
        self.assertIsNone(image)
        self.assertEqual(0.0, received_at)
        self.assertEqual("已断开投屏", session.stats().status)

    def test_xlv3_frame_orientation_is_unchanged(self) -> None:
        image = Image.new("RGB", (2, 3), "green")
        self.assertIs(image, prepare_xlv3_image(image))

    def test_process_memory_metric(self) -> None:
        self.assertGreater(working_set_mb(), 0.0)

    def test_process_load_metric(self) -> None:
        sampler = ProcessLoadSampler()
        self.assertGreaterEqual(sampler.sample_percent(), 0.0)


if __name__ == "__main__":
    unittest.main()
