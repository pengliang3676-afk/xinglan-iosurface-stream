from __future__ import annotations

import unittest
from pathlib import Path


PROJECT = Path(__file__).resolve().parent.parent


class PhoneTouchStreamSourceTests(unittest.TestCase):
    def test_stream_move_is_advertised_and_has_no_ack(self) -> None:
        protocol = (PROJECT / "phone" / "XLControlProtocol.h").read_text(
            encoding="utf-8"
        )
        server = (PROJECT / "phone" / "XLControlServer.mm").read_text(
            encoding="utf-8"
        )
        self.assertIn("XLMessageTouchStream = 15", protocol)
        self.assertIn("XLCapabilityTouchStream", protocol)
        block = server.split("case XLMessageTouchStream:", 1)[1].split(
            "case XLMessageSystemAction:", 1
        )[0]
        self.assertIn("XLHandleTouch(sender, payload)", block)
        self.assertNotIn("XLWriteAck", block)


if __name__ == "__main__":
    unittest.main()
