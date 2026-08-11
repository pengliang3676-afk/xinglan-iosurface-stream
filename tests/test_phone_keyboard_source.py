from __future__ import annotations

import unittest
from pathlib import Path

PROJECT = Path(__file__).resolve().parent.parent


class PhoneKeyboardSourceTests(unittest.TestCase):
    def test_key_modifiers_travel_in_header_flags(self) -> None:
        server = (PROJECT / "phone" / "XLControlServer.mm").read_text(encoding="utf-8")
        sender = (PROJECT / "phone" / "XLHIDSender.mm").read_text(encoding="utf-8")
        self.assertIn("ntohs(header.flags)", server)
        self.assertIn("XLKeyModifierShift", sender)
        self.assertIn("usage:usages[index] down:YES", sender)

    def test_direct_paste_never_writes_phone_clipboard(self) -> None:
        source = (PROJECT / "phone" / "XLSystemActions.xm").read_text(encoding="utf-8")
        start = source.index("static BOOL XLCommitTextIntoFocusedControl")
        end = source.index("static void XLPostPasteAck", start)
        implementation = source[start:end]
        self.assertIn("XLInsertTextIntoFocusedControl(text)", implementation)
        self.assertNotIn("UIPasteboard", implementation)
        self.assertNotIn("paste:", implementation)


if __name__ == "__main__":
    unittest.main()
