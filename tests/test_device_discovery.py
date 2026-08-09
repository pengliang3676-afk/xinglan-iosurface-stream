from __future__ import annotations

import sys
import unittest
from pathlib import Path
from unittest.mock import patch

PROJECT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT))

from xinglan.device_discovery import discover_usb_udids_stable


class StableDeviceDiscoveryTests(unittest.TestCase):
    @patch("xinglan.device_discovery.time.sleep", return_value=None)
    @patch("xinglan.device_discovery.discover_usb_udids")
    def test_merges_transiently_missing_devices(self, discover, _sleep) -> None:
        discover.side_effect = [
            ["phone-a", "phone-b"],
            ["phone-a", "phone-c"],
            ["phone-a", "phone-b", "phone-c"],
        ]
        self.assertEqual(
            ["phone-a", "phone-b", "phone-c"],
            discover_usb_udids_stable(PROJECT),
        )

    @patch("xinglan.device_discovery.time.sleep", return_value=None)
    @patch("xinglan.device_discovery.discover_usb_udids")
    def test_keeps_successful_snapshot_when_a_retry_fails(self, discover, _sleep) -> None:
        discover.side_effect = [["phone-a"], RuntimeError("busy"), ["phone-b"]]
        self.assertEqual(
            ["phone-a", "phone-b"],
            discover_usb_udids_stable(PROJECT),
        )


if __name__ == "__main__":
    unittest.main()
