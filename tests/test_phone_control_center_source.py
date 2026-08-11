from __future__ import annotations

import sys
import unittest
from pathlib import Path

PROJECT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT))


class PhoneControlCenterSourceTests(unittest.TestCase):
    def test_phone_protocol_has_dedicated_control_center_action(self) -> None:
        header = (PROJECT / "phone" / "XLControlProtocol.h").read_text(
            encoding="utf-8"
        )
        server = (PROJECT / "phone" / "XLControlServer.mm").read_text(
            encoding="utf-8"
        )
        self.assertIn("XLSystemActionControlCenter = 6", header)
        self.assertIn("XLSystemActionControlCenter", server)
        self.assertIn("com.jibeib.xlstream.controlcenter.open", server)

    def test_springboard_opens_control_center_without_touch_swipe(self) -> None:
        source = (PROJECT / "phone" / "XLSystemActions.xm").read_text(
            encoding="utf-8"
        )
        self.assertIn('NSClassFromString(@"SBControlCenterController")', source)
        self.assertIn('@"presentAnimated:completion:"', source)
        self.assertIn("XLControlCenterOpenNotification", source)


if __name__ == "__main__":
    unittest.main()
