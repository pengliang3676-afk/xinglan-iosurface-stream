from __future__ import annotations

import unittest

from app import XinglanApp
from xinglan.control_protocol import SystemAction


class Value:
    def __init__(self, value=False) -> None:
        self.value = value

    def get(self):
        return self.value

    def set(self, value) -> None:
        self.value = value


class ConfigRecorder:
    def __init__(self) -> None:
        self.values: dict[str, object] = {}

    def configure(self, **values: object) -> None:
        self.values.update(values)


class Session:
    def __init__(self, udid: str) -> None:
        self.udid = udid
        self.touches: list[tuple[int, float, float]] = []
        self.keys: list[tuple[int, int, int]] = []
        self.texts: list[str] = []
        self.system_actions: list[SystemAction] = []

    def send_touch(self, kind: int, x: float, y: float) -> bool:
        self.touches.append((kind, x, y))
        return True

    def send_key(self, page: int, usage: int, modifiers: int = 0) -> bool:
        self.keys.append((page, usage, modifiers))
        return True

    def send_text(self, text: str) -> bool:
        self.texts.append(text)
        return True

    def send_system_action(self, action: SystemAction) -> bool:
        self.system_actions.append(action)
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
    app._ime_source = None
    app._ime_from_master = False
    app.device_label = lambda udid: udid
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

    def test_small_window_system_shortcut_never_broadcasts(self) -> None:
        app, sessions = make_app(sync=True)
        app.route_single_system_action(sessions["master"], SystemAction.HOME)
        self.assertEqual([SystemAction.HOME], sessions["master"].system_actions)
        self.assertEqual([], sessions["checked"].system_actions)
        self.assertEqual([], sessions["unchecked"].system_actions)

    def test_small_window_control_center_action_never_broadcasts(self) -> None:
        app, sessions = make_app(sync=True)
        app.open_single_control_center(sessions["master"])
        self.assertEqual(
            [SystemAction.CONTROL_CENTER], sessions["master"].system_actions
        )
        self.assertEqual([], sessions["checked"].system_actions)
        self.assertEqual([], sessions["unchecked"].system_actions)

    def test_master_printable_key_uses_unicode_and_syncs_only_to_checked_peer(self) -> None:
        app, sessions = make_app(sync=True)
        result = app.route_keypress(
            sessions["master"], "a", "a", from_master=True
        )
        self.assertEqual("break", result)
        self.assertEqual(["a"], sessions["master"].texts)
        self.assertEqual(["a"], sessions["checked"].texts)
        self.assertEqual([], sessions["unchecked"].texts)
        self.assertEqual([], sessions["master"].keys)

    def test_small_window_printable_key_never_broadcasts(self) -> None:
        app, sessions = make_app(sync=True)
        app.route_keypress(sessions["master"], "A", "A", from_master=False)
        self.assertEqual(["A"], sessions["master"].texts)
        self.assertEqual([], sessions["checked"].texts)

    def test_non_printable_key_still_uses_keyboard_hid(self) -> None:
        app, sessions = make_app(sync=False)
        app.route_keypress(sessions["master"], "BackSpace", "", from_master=True)
        self.assertEqual([(0x07, 0x2A, 0)], sessions["master"].keys)
        self.assertEqual([], sessions["master"].texts)

    def test_worker_text_from_master_syncs_only_to_checked_peer(self) -> None:
        app, sessions = make_app(sync=True)
        app._ime_source = sessions["master"]
        app._ime_from_master = True
        app._route_ime_text("中文 123")
        self.assertEqual(["中文 123"], sessions["master"].texts)
        self.assertEqual(["中文 123"], sessions["checked"].texts)
        self.assertEqual([], sessions["unchecked"].texts)

    def test_worker_text_from_small_window_never_broadcasts(self) -> None:
        app, sessions = make_app(sync=True)
        app._ime_source = sessions["master"]
        app._ime_from_master = False
        app._route_ime_text("abc")
        self.assertEqual(["abc"], sessions["master"].texts)
        self.assertEqual([], sessions["checked"].texts)

    def test_worker_key_uses_same_target_selection(self) -> None:
        app, sessions = make_app(sync=True)
        app._ime_source = sessions["master"]
        app._ime_from_master = True
        app._route_ime_key(0x07, 0x2A, "退格")
        self.assertEqual([(0x07, 0x2A, 0)], sessions["master"].keys)
        self.assertEqual([(0x07, 0x2A, 0)], sessions["checked"].keys)
        self.assertEqual([], sessions["unchecked"].keys)

    def test_activate_worker_keeps_selected_phone_and_position(self) -> None:
        app, sessions = make_app(sync=False)

        class Worker:
            activated: tuple[int, int] | None = None

            def activate(self, x: int, y: int) -> None:
                self.activated = (x, y)

        app.ime_worker = Worker()
        app.activate_ime(
            sessions["checked"],
            from_master=False,
            screen_x=100,
            screen_y=200,
        )
        self.assertIs(sessions["checked"], app._ime_source)
        self.assertFalse(app._ime_from_master)
        self.assertEqual((108, 228), app.ime_worker.activated)

    def test_group_button_never_changes_colour_during_focus_switch(self) -> None:
        app, _ = make_app(sync=False)
        app.group_button = ConfigRecorder()
        app._current_group_udids = lambda: ["master"]

        app._refresh_group_button()

        self.assertEqual("断开本组", app.group_button.values["text"])
        self.assertEqual(app.group_button.values["bg"], app.group_button.values["activebackground"])

    def test_group_menu_selection_changes_group_exactly_once(self) -> None:
        app, _ = make_app(sync=False)
        app.group_var = Value("第1组")
        app.group_popup = None
        changes: list[str] = []
        app._group_changed = lambda _event=None: changes.append(app.group_var.get())

        app._set_group_menu_values(["第1组", "第2组"])
        app._choose_group_from_popup("第2组")

        self.assertEqual(["第1组", "第2组"], app.group_values)
        self.assertEqual("第2组", app.group_var.get())
        self.assertEqual(["第2组"], changes)

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
