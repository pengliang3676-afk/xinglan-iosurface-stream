from __future__ import annotations

import unittest
from pathlib import Path


PROJECT = Path(__file__).resolve().parent.parent


class PhoneTouchLifecycleSourceTests(unittest.TestCase):
    def test_xlstream_native_touch_backend_is_removed(self) -> None:
        protocol = (PROJECT / "phone" / "XLControlProtocol.h").read_text(
            encoding="utf-8"
        )
        server = (PROJECT / "phone" / "XLControlServer.mm").read_text(
            encoding="utf-8"
        )
        sender = (PROJECT / "phone" / "XLHIDSender.mm").read_text(
            encoding="utf-8"
        )
        self.assertNotIn("XLMessageTouch", protocol)
        self.assertNotIn("XLCapabilityTouch", protocol)
        self.assertNotIn("XLTouchPayload", protocol)
        self.assertNotIn("XLHandleTouch", server)
        self.assertNotIn("sendTouchPhase", sender)


if __name__ == "__main__":
    unittest.main()
