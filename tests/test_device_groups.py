from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path


PROJECT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT))

from xinglan.device_groups import DeviceGroupStore


class DeviceGroupStoreTests(unittest.TestCase):
    def test_connected_devices_are_not_automatically_assigned(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "groups.json"
            store = DeviceGroupStore(
                path, max_groups=6, group_size=10
            )
            self.assertEqual({}, store.assignments)
            self.assertIsNone(store.assignment("phone-01"))
            self.assertFalse(path.exists())

    def test_saved_positions_survive_restart_and_missing_phone(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "groups.json"
            store = DeviceGroupStore(path, max_groups=6, group_size=10)
            store.assign("phone-a", 1, 1)
            store.assign("phone-b", 1, 2)
            store.assign("phone-c", 1, 3)

            reloaded = DeviceGroupStore(path, max_groups=6, group_size=10)
            self.assertEqual(
                [(1, "phone-a"), (3, "phone-c")],
                reloaded.positioned_devices(["phone-c", "phone-a"], 1),
            )

    def test_duplicate_group_slot_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            store = DeviceGroupStore(
                Path(directory) / "groups.json", max_groups=6, group_size=10
            )
            store.assign("phone-a", 1, 1)
            store.assign("phone-b", 1, 2)
            with self.assertRaisesRegex(ValueError, "已被手机"):
                store.assign("phone-b", 1, 1, "二号机")

    def test_custom_name_is_persisted(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "groups.json"
            store = DeviceGroupStore(path, max_groups=6, group_size=10)
            store.assign("phone-a", 2, 4, "客厅04")

            reloaded = DeviceGroupStore(path, max_groups=6, group_size=10)
            assignment = reloaded.assignment("phone-a")
            self.assertEqual((2, 4, "客厅04"), (
                assignment.group,
                assignment.slot,
                assignment.name,
            ))


if __name__ == "__main__":
    unittest.main()
