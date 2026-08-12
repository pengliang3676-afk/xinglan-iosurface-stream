from __future__ import annotations

import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


class PhoneHomeActionSourceTests(unittest.TestCase):
    def test_desktop_uses_one_shared_home_action_for_master_and_tiles(self) -> None:
        app = (ROOT / "app.py").read_text(encoding="utf-8")
        self.assertIn("route_single_system_action(\n                    self.session, SystemAction.HOME", app)
        self.assertIn('(\"主屏\", lambda: self.route_system_action(SystemAction.HOME))', app)

    def test_phone_deduplicates_system_action_across_reconnect(self) -> None:
        server = (ROOT / "phone" / "XLControlServer.mm").read_text(encoding="utf-8")
        self.assertIn("XLLastSystemActionSequence == sequence", server)
        self.assertIn("XLLastSystemActionValue == rawAction", server)
        self.assertIn("XLHandleSystemAction(sender, payload, sequence)", server)

    def test_home_is_noop_when_springboard_reports_ordinary_home(self) -> None:
        server = (ROOT / "phone" / "XLControlServer.mm").read_text(encoding="utf-8")
        springboard = (ROOT / "phone" / "XLSystemActions.xm").read_text(
            encoding="utf-8"
        )
        self.assertIn("if (XLIsOrdinaryHomeScreen() == 1) return 0;", server)
        self.assertIn("com.jibeib.xlstream.home.state.request", server)
        self.assertIn("com.jibeib.xlstream.home.state.request", springboard)
        self.assertIn("_accessibilityFrontMostApplication", springboard)
        self.assertIn("notify_set_state(ackToken, 0)", server)
        self.assertIn("XLPostHomeStateAck(request, XLIsOrdinaryHomeScreen())", springboard)

    def test_desktop_sequence_starts_randomly(self) -> None:
        session = (ROOT / "xinglan" / "session.py").read_text(encoding="utf-8")
        self.assertIn("self._sequence = secrets.randbits(32)", session)


if __name__ == "__main__":
    unittest.main()
