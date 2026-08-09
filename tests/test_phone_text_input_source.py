from pathlib import Path
import unittest


ROOT = Path(__file__).resolve().parents[1]


class PhoneTextInputSourceTests(unittest.TestCase):
    def test_text_input_prefers_foreground_app_paste(self):
        source = (ROOT / "phone" / "XLControlServer.mm").read_text(encoding="utf-8")
        handler = source[source.index("static uint32_t XLHandleTextInput") :]
        self.assertIn("XLPostPasteText(text)", handler)
        self.assertIn("XLPostTextScalars(text)", handler)
        self.assertLess(
            handler.index("XLPostPasteText(text)"),
            handler.index("XLPostTextScalars(text)"),
        )

    def test_foreground_tweak_reassembles_and_pastes_complete_text(self):
        source = (ROOT / "phone" / "XLSystemActions.xm").read_text(encoding="utf-8")
        self.assertIn("XLTextPasteBeginNotification", source)
        self.assertIn("XLTextPasteChunkNotification", source)
        self.assertIn("XLTextPasteCommitNotification", source)
        self.assertIn("UIPasteboard.generalPasteboard.string = text", source)
        self.assertIn("XLPasteIntoFocusedControl()", source)

    def test_paste_shortcut_has_modifier_timing(self):
        source = (ROOT / "phone" / "XLHIDSender.mm").read_text(encoding="utf-8")
        method = source[source.index("- (BOOL)sendPasteShortcut") :]
        self.assertIn("usage:0xE3 down:YES", method)
        self.assertIn("usage:0x19", method)
        self.assertIn("usage:0xE3 down:NO", method)
        self.assertGreaterEqual(method.count("usleep("), 2)

    def test_package_version_matches_app_version(self):
        control = (ROOT / "phone" / "control").read_text(encoding="utf-8")
        info = (ROOT / "phone" / "layout" / "Applications" / "XLStream.app" / "Info.plist").read_text(
            encoding="utf-8"
        )
        self.assertIn("Version: 0.5.0", control)
        self.assertIn("<string>0.5.0</string>", info)
        self.assertIn("<string>50</string>", info)


if __name__ == "__main__":
    unittest.main()
