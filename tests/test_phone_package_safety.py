from __future__ import annotations

import plistlib
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
PHONE = ROOT / "phone"


def read_control() -> dict[str, str]:
    fields: dict[str, str] = {}
    for line in (PHONE / "control").read_text(encoding="utf-8").splitlines():
        if ":" not in line:
            continue
        key, value = line.split(":", 1)
        fields[key] = value.strip()
    return fields


class PhonePackageSafetyTests(unittest.TestCase):
    def test_daemon_supplies_legacy_power_port_without_stealing_healthy_service(self) -> None:
        main = (PHONE / "main.mm").read_text(encoding="utf-8")
        control = (PHONE / "XLControlServer.mm").read_text(encoding="utf-8")
        self.assertIn("XLStartLegacyControlCompatibilityServer", main)
        self.assertIn("XLLegacyControlPort = 6000", control)
        self.assertIn("[line isEqualToString:@\"14\"]", control)
        self.assertIn("[line isEqualToString:@\"15\"]", control)
        self.assertIn("bind(server, (struct sockaddr *)&address", control)
        self.assertEqual(1, control.count("XLRunLegacyControlCompatibilityServer();"))

    def test_safe_package_identity_replaces_legacy_package_without_conflict(self) -> None:
        fields = read_control()
        self.assertEqual("com.jibeib.xlstream.safe", fields["Package"])
        self.assertEqual("com.jibeib.xlstream", fields["Provides"])
        self.assertEqual("com.jibeib.xlstream", fields["Replaces"])
        self.assertNotIn("Conflicts", fields)
        self.assertNotIn("Breaks", fields)

    def test_package_has_no_dpkg_maintainer_scripts(self) -> None:
        metadata = PHONE / "layout" / "DEBIAN"
        forbidden = ("preinst", "postinst", "prerm", "postrm", "config")
        self.assertFalse(
            [name for name in forbidden if (metadata / name).exists()],
            "phone package must not run service or UI actions inside dpkg",
        )

    def test_bundle_version_matches_package_version(self) -> None:
        fields = read_control()
        info_path = PHONE / "layout" / "Applications" / "XLStream.app" / "Info.plist"
        with info_path.open("rb") as stream:
            info = plistlib.load(stream)
        self.assertEqual(fields["Version"], info["CFBundleShortVersionString"])

    def test_springboard_starts_service_without_dpkg_scripts(self) -> None:
        actions = (PHONE / "XLSystemActions.xm").read_text(encoding="utf-8")
        launcher = (PHONE / "XLLauncher.mm").read_text(encoding="utf-8")
        self.assertIn("XLStartStreamServiceAfterSpringBoard", actions)
        self.assertIn('/Applications/XLStream.app/XLStreamLauncher', actions)
        self.assertIn("posix_spawn(&process", actions)
        self.assertIn("XLAcquireLauncherLock", launcher)
        self.assertIn("LOCK_EX | LOCK_NB", launcher)
        self.assertIn("XLServiceIsAlreadyRunning", launcher)
        self.assertIn("XLPortIsListening(6203)", launcher)

        daemon = (PHONE / "main.mm").read_text(encoding="utf-8")
        self.assertIn("XLAcquireServiceLock", daemon)
        self.assertIn(".xlstream-service.lock", daemon)
        self.assertIn("return 73", daemon)

    def test_app_is_visible_and_has_the_selected_icon(self) -> None:
        info_path = PHONE / "layout" / "Applications" / "XLStream.app" / "Info.plist"
        with info_path.open("rb") as stream:
            info = plistlib.load(stream)
        self.assertEqual("星澜", info["CFBundleDisplayName"])
        self.assertEqual("星澜", info["CFBundleName"])
        self.assertNotIn("SBAppTags", info)
        for icon_name in ("Icon.png", "Icon@2x.png", "Icon@3x.png"):
            self.assertIn(icon_name, info["CFBundleIconFiles"])
            self.assertTrue((info_path.parent / icon_name).is_file())

    def test_package_uses_xlstream_hid_without_ioscpy_runtime(self) -> None:
        makefile = (PHONE / "Makefile").read_text(encoding="utf-8")
        self.assertIn("XLHIDSender.mm", makefile)
        self.assertNotIn("xltouchd", makefile)
        self.assertNotIn("XLTouchActions", makefile)
        ioscpy_root = PHONE / "vendor" / "ioscpy"
        self.assertFalse(
            ioscpy_root.exists() and any(path.is_file() for path in ioscpy_root.rglob("*"))
        )
        self.assertFalse(
            (
                PHONE
                / "layout"
                / "Applications"
                / "XLStream.app"
                / "ioscpy-LICENSE.txt"
            ).exists()
        )
        self.assertFalse((
            PHONE
            / "layout"
            / "Library"
            / "LaunchDaemons"
            / "com.jibeib.xltouchd.plist"
        ).exists())

    def test_folder_transfer_protocol_is_bounded_and_path_safe(self) -> None:
        source = (PHONE / "XLFileTransferServer.mm").read_text(encoding="utf-8")
        self.assertIn('memcmp(header, "XLFD", 4)', source)
        self.assertIn("XLMaximumFolderMetadataSize", source)
        self.assertIn("XLMaximumFolderEntries", source)
        self.assertIn("XLSafeRelativePath", source)
        self.assertIn("NSCharacterSet.controlCharacterSet", source)
        self.assertIn("removeItemAtPath:rootDestination", source)
        self.assertIn("listen(server, 16)", source)


if __name__ == "__main__":
    unittest.main()
