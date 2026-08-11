from pathlib import Path
import unittest


ROOT = Path(__file__).resolve().parents[1]


class PhoneTextInputSourceTests(unittest.TestCase):
    def test_text_input_requires_foreground_app_ack(self):
        source = (ROOT / "phone" / "XLControlServer.mm").read_text(encoding="utf-8")
        handler = source[source.index("static uint32_t XLHandleTextInput") :]
        self.assertIn("XLPostPasteText(text)", handler)
        self.assertIn("XLTextPasteAckNotification", source)
        self.assertIn("(ackState >> 1) == request", source)
        self.assertNotIn("XLPostTextScalars(text) ? 0", handler)

    def test_foreground_tweak_reassembles_and_directly_inserts_complete_text(self):
        source = (ROOT / "phone" / "XLSystemActions.xm").read_text(encoding="utf-8")
        self.assertIn("XLTextPasteBeginNotification", source)
        self.assertIn("XLTextPasteChunkNotification", source)
        self.assertIn("XLTextPasteCommitNotification", source)
        self.assertIn("XLTextPasteAckNotification", source)
        self.assertNotIn("UIPasteboard.generalPasteboard.string = text", source)
        self.assertIn("UIKeyboardImpl", source)
        self.assertIn("XLInsertTextIntoFocusedControl(text)", source)
        self.assertIn("XLPostPasteAck(request", source)

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
        self.assertIn("Version: 0.5.3", control)
        self.assertIn("<string>0.5.3</string>", info)
        self.assertIn("<string>53</string>", info)

    def test_package_scripts_never_wait_for_launchctl(self):
        scripts = ROOT / "phone" / "layout" / "DEBIAN"
        for name in ("postinst", "prerm"):
            source = (scripts / name).read_text(encoding="utf-8")
            self.assertNotIn("run_bounded", source)
            self.assertNotRegex(source, r"(?m)^\s*wait\b")
            self.assertIn("</dev/null >/dev/null 2>&1 &", source)


if __name__ == "__main__":
    unittest.main()
