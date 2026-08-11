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
        self.keys: list[tuple[int, int, int]] = []
        self.texts: list[str] = []

    def send_touch(self, kind: int, x: float, y: float) -> bool:
        self.touches.append((kind, x, y))
        return True

    def send_key(self, page: int, usage: int, modifiers: int = 0) -> bool:
        self.keys.append((page, usage, modifiers))
        return True

    def send_text(self, text: str) -> bool:
        self.texts.append(text)
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

    def test_master_hid_key_syncs_only_to_checked_peer(self) -> None:
        app, sessions = make_app(sync=True)
        result = app.route_keypress(
            sessions["master"], "a", "a", from_master=True
        )
        self.assertEqual("break", result)
        self.assertEqual([(0x07, 0x04, 0)], sessions["master"].keys)
        self.assertEqual([(0x07, 0x04, 0)], sessions["checked"].keys)
        self.assertEqual([], sessions["unchecked"].keys)

    def test_small_window_hid_key_never_broadcasts(self) -> None:
        app, sessions = make_app(sync=True)
        app.route_keypress(sessions["master"], "A", "A", from_master=False)
        self.assertEqual([(0x07, 0x04, 2)], sessions["master"].keys)
        self.assertEqual([], sessions["checked"].keys)

    def test_clipboard_text_uses_existing_bounded_control_queue(self) -> None:
        app, sessions = make_app(sync=False)

        class Root:
            @staticmethod
            def clipboard_get() -> str:
                return "中文 ABC 123"

        app.root = Root()
        app.route_clipboard_paste(sessions["master"], from_master=True)
        self.assertEqual(["中文 ABC 123"], sessions["master"].texts)


if __name__ == "__main__":
    unittest.main()
