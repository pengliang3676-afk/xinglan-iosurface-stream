from pathlib import Path
import unittest


ROOT = Path(__file__).resolve().parents[1]


class PhoneTouchSourceTests(unittest.TestCase):
    def test_touch_lifecycle_matches_trollvnc_hid_semantics(self) -> None:
        source = (ROOT / "phone" / "XLHIDSender.mm").read_text(encoding="utf-8")
        method = source[source.index("- (BOOL)sendTouchPhase:") : source.index("- (BOOL)sendKeyboardPage:")]

        self.assertIn("XLDigitizerEventTouch | XLDigitizerEventIdentity", method)
        self.assertIn("XLDigitizerEventPosition | XLDigitizerEventAttribute", method)
        self.assertIn("XLDigitizerEventCancel", method)
        self.assertIn("XLTouchIdentifierForFinger(finger)", method)
        self.assertIn("double pathPressure = 0.0", method)
        self.assertIn("double pathRadius = touching ? 5.0 : 0.0", method)
        self.assertNotIn("_setIntegerValue(parent, XLDigitizerRange, 1)", method)
        self.assertNotIn("_setIntegerValue(parent, XLDigitizerTouch, 1)", method)


if __name__ == "__main__":
    unittest.main()
