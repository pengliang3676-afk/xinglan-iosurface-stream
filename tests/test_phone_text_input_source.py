from pathlib import Path
import plistlib
import re
import unittest


ROOT = Path(__file__).resolve().parents[1]


class PhoneTextInputSourceTests(unittest.TestCase):
    def test_text_input_uses_daemon_unicode_hid(self):
        source = (ROOT / "phone" / "XLControlServer.mm").read_text(encoding="utf-8")
        handler = source[source.index("static uint32_t XLHandleTextInput") :]
        self.assertIn("[sender sendUnicodeText:text]", handler)
        self.assertNotIn("XLPostPasteText", source)
        self.assertNotIn("XLTextPasteAckNotification", source)

    def test_unicode_hid_uses_utf16_sender_metadata_and_chunks(self):
        source = (ROOT / "phone" / "XLHIDSender.mm").read_text(encoding="utf-8")
        self.assertIn('dlsym(_ioKitHandle, "IOHIDEventCreateUnicodeEvent")', source)
        self.assertIn("NSUTF16LittleEndianStringEncoding", source)
        self.assertIn("XLUnicodeEncodingUTF16LE", source)
        self.assertIn("_setSenderID(event, XLSyntheticSenderID)", source)
        self.assertIn("NSStringEnumerationByComposedCharacterSequences", source)

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
        info_path = ROOT / "phone" / "layout" / "Applications" / "XLStream.app" / "Info.plist"
        with info_path.open("rb") as stream:
            info = plistlib.load(stream)
        match = re.search(r"(?m)^Version:\s*(\S+)\s*$", control)
        self.assertIsNotNone(match)
        self.assertEqual(match.group(1), info["CFBundleShortVersionString"])
        self.assertGreater(int(info["CFBundleVersion"]), 0)

    def test_package_scripts_never_wait_for_launchctl(self):
        scripts = ROOT / "phone" / "layout" / "DEBIAN"
        self.assertFalse(
            any(scripts.iterdir()) if scripts.exists() else False,
            "safe package must not contain dpkg scripts",
        )


if __name__ == "__main__":
    unittest.main()
