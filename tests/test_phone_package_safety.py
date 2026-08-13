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

    def test_integrated_ioscpy_touch_has_private_runtime_and_license(self) -> None:
        makefile = (PHONE / "Makefile").read_text(encoding="utf-8")
        protocol = (
            PHONE / "vendor" / "ioscpy" / "daemon" / "Protocol.h"
        ).read_text(encoding="utf-8")
        self.assertIn("xltouchd", makefile)
        self.assertIn("XLTouchActions", makefile)
        self.assertIn("IOSPY_DEFAULT_PORT     27185", protocol)
        self.assertIn("IOSPY_FRAME_PORT       27186", protocol)
        self.assertTrue((PHONE / "vendor" / "ioscpy" / "LICENSE").is_file())
        self.assertTrue(
            (
                PHONE
                / "layout"
                / "Applications"
                / "XLStream.app"
                / "ioscpy-LICENSE.txt"
            ).is_file()
        )

    def test_integrated_touch_launches_without_maintainer_script(self) -> None:
        plist_path = (
            PHONE
            / "layout"
            / "Library"
            / "LaunchDaemons"
            / "com.jibeib.xltouchd.plist"
        )
        with plist_path.open("rb") as stream:
            launch = plistlib.load(stream)
        self.assertEqual("com.jibeib.xltouchd", launch["Label"])
        self.assertEqual(
            "/Applications/XLStream.app/bin/xltouchd",
            launch["ProgramArguments"][0],
        )


if __name__ == "__main__":
    unittest.main()
