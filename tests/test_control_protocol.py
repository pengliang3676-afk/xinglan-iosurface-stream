from __future__ import annotations

import sys
import unittest
from pathlib import Path

PROJECT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT))

from xinglan.control_protocol import (
    CONTROL_MAGIC,
    HEADER,
    MessageType,
    TouchCommand,
    TouchPhase,
    pack_header,
    pack_touch,
    unpack_header,
    unpack_touch,
)


class ControlProtocolTests(unittest.TestCase):
    def test_header_is_fixed_16_bytes(self) -> None:
        encoded = pack_header(CONTROL_MAGIC, MessageType.PING, 8, 77)
        self.assertEqual(16, len(encoded))
        self.assertEqual(HEADER.size, len(encoded))
        header = unpack_header(encoded, CONTROL_MAGIC)
        self.assertEqual((MessageType.PING, 8, 77), (
            header.message_type, header.payload_length, header.sequence
        ))

    def test_touch_round_trip_and_clamp(self) -> None:
        packet = pack_touch(
            TouchCommand(TouchPhase.MOVE, 1, -0.5, 1.5, 0.4, 12345),
            sequence=9,
        )
        header = unpack_header(packet[:HEADER.size], CONTROL_MAGIC)
        touch = unpack_touch(packet[HEADER.size:])
        self.assertEqual(MessageType.TOUCH, header.message_type)
        self.assertEqual(12, header.payload_length)
        self.assertEqual((0.0, 1.0), (touch.x, touch.y))
        self.assertAlmostEqual(0.4, touch.pressure, places=4)

    def test_rejects_wrong_magic(self) -> None:
        encoded = pack_header(CONTROL_MAGIC, MessageType.PING, 0, 1)
        with self.assertRaises(ValueError):
            unpack_header(encoded, b"XLS1")

    def test_rejects_oversized_payload(self) -> None:
        with self.assertRaises(ValueError):
            pack_header(CONTROL_MAGIC, MessageType.PING, 1024 * 1024 + 1, 1)


if __name__ == "__main__":
    unittest.main()
