from __future__ import annotations

import unittest

from app import XinglanApp


class Value:
    def __init__(self, value=False) -> None:
        self.value = value

    def get(self):
        return self.value

    def set(self, value) -> None:
        self.value = value


class Session:
    def __init__(self, udid: str) -> None:
        self.udid = udid
        self.touches: list[tuple[int, float, float]] = []

    def send_touch(self, kind: int, x: float, y: float) -> bool:
        self.touches.append((kind, x, y))
        return True


def make_app(sync: bool) -> tuple[XinglanApp, dict[str, Session]]:
    app = XinglanApp.__new__(XinglanApp)
    sessions = {udid: Session(udid) for udid in ("master", "checked", "unchecked")}
    app.sessions = sessions
    app.active_udids = set(sessions)
    app.selected_udids = {"checked"}
    app.master_udid = "master"
    app.sync_enabled = Value(sync)
    app.summary = Value("")
    app._current_group_udids = lambda: ["master", "checked", "unchecked"]
    return app, sessions


class SyncSelectionTests(unittest.TestCase):
    def test_control_buttons_target_only_master_when_sync_is_off(self) -> None:
        app, _ = make_app(sync=False)
        self.assertEqual(["master"], app._selected_control_udids())

    def test_control_buttons_target_only_checked_devices_when_sync_is_on(self) -> None:
        app, _ = make_app(sync=True)
        self.assertEqual(["checked"], app._selected_control_udids())

    def test_master_touch_syncs_only_to_checked_peer(self) -> None:
        app, sessions = make_app(sync=True)
        app.route_touch(sessions["master"], 1, 0.25, 0.75, from_master=True)
        self.assertEqual(1, len(sessions["master"].touches))
        self.assertEqual(1, len(sessions["checked"].touches))
        self.assertEqual(0, len(sessions["unchecked"].touches))

    def test_small_window_touch_never_broadcasts(self) -> None:
        app, sessions = make_app(sync=True)
        app.route_touch(sessions["master"], 1, 0.25, 0.75, from_master=False)
        self.assertEqual(1, len(sessions["master"].touches))
        self.assertEqual(0, len(sessions["checked"].touches))
        self.assertEqual(0, len(sessions["unchecked"].touches))


if __name__ == "__main__":
    unittest.main()
