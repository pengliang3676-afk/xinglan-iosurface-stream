from __future__ import annotations

import unittest
from pathlib import Path


PROJECT = Path(__file__).resolve().parent.parent


class PhoneTouchLifecycleSourceTests(unittest.TestCase):
    def test_trollvnc_style_touch_lifecycle_is_preserved(self) -> None:
        source = (PROJECT / "phone" / "XLHIDSender.mm").read_text(
            encoding="utf-8"
        )
        self.assertIn("XLTouchIdentifierForFinger", source)
        self.assertIn(
            "eventMask = XLDigitizerEventPosition | XLDigitizerEventAttribute",
            source,
        )
        self.assertIn("double pathRadius = touching ? 5.0 : 0.0", source)
        self.assertNotIn("_setIntegerValue(parent, XLDigitizerTouch, 1)", source)
        self.assertNotIn("_setIntegerValue(parent, XLDigitizerRange, 1)", source)


if __name__ == "__main__":
    unittest.main()
