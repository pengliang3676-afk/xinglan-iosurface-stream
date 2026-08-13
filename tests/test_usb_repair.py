from __future__ import annotations

import sys
import unittest
from pathlib import Path
from unittest.mock import patch

PROJECT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT))

from xinglan.usb_repair import (
    WindowsIphone,
    build_repair_plan,
    discover_windows_iphone_count,
    normalize_udid,
)


class UsbRepairTests(unittest.TestCase):
    @patch("xinglan.usb_repair._powershell_json", return_value=60)
    def test_lightweight_windows_count_returns_present_iphones(self, _mock_query) -> None:
        self.assertEqual(60, discover_windows_iphone_count())

    def test_normalize_udid_matches_usbmux_and_pnp_forms(self) -> None:
        self.assertEqual(
            normalize_udid("00008030-0001111426FA402E"),
            normalize_udid("000080300001111426FA402E"),
        )

    def test_missing_phones_are_grouped_by_external_hub(self) -> None:
        phones = [
            WindowsIphone("A1", r"USB\VID_1A40&PID_0101\HUB-A", "USB 2.0 Hub"),
            WindowsIphone("A2", r"USB\VID_1A40&PID_0101\HUB-A", "USB 2.0 Hub"),
            WindowsIphone("B1", r"USB\VID_1A40&PID_0101\HUB-B", "USB 2.0 Hub"),
        ]
        plan = build_repair_plan(["B1"], phones)
        self.assertEqual(3, plan.windows_count)
        self.assertEqual(1, plan.usbmux_count)
        self.assertEqual(("A1", "A2"), plan.missing_serials)
        self.assertEqual(1, len(plan.branches))
        self.assertEqual(("A1", "A2"), plan.branches[0].missing_serials)

    def test_root_hub_is_never_cycled(self) -> None:
        phone = WindowsIphone(
            "A1",
            r"USB\ROOT_HUB30\4&1234&0&0",
            "USB Root Hub (USB 3.0)",
        )
        plan = build_repair_plan([], [phone])
        self.assertEqual((), plan.branches)
        self.assertEqual(("A1",), plan.unsupported_serials)


if __name__ == "__main__":
    unittest.main()
