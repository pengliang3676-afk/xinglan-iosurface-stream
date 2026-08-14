from __future__ import annotations

import asyncio
import unittest
from types import SimpleNamespace

from app import DeviceTile, MasterView
from xinglan.session import DeviceSession
from xinglan.trollvnc_touch import NoVncPointerScheduler, PointerSample


class TrollVNCTouchSessionTests(unittest.TestCase):
    def setUp(self) -> None:
        self.loop = asyncio.new_event_loop()
        self.session = DeviceSession("test-touch-device")

        async def initialize() -> None:
            current = asyncio.get_running_loop()
            self.session._loop = current
            self.session._control_queue = asyncio.Queue(maxsize=64)
            self.session._rfb_touch_queue = asyncio.Queue(maxsize=64)
            self.session._rfb_touch_scheduler = NoVncPointerScheduler(
                current,
                self.session._enqueue_rfb_pointer,
            )
            self.session._set_touch_online(True)

        self.loop.run_until_complete(initialize())

    def tearDown(self) -> None:
        scheduler = self.session._rfb_touch_scheduler
        if scheduler is not None:
            scheduler.reset()
        self.loop.close()

    def test_public_touch_path_uses_rfb_queue_not_native_control(self) -> None:
        async def submit() -> None:
            self.assertTrue(self.session.send_touch(1, 0.1, 0.2))
            for index in range(50):
                self.assertTrue(
                    self.session.send_touch(2, 0.2 + index / 100.0, 0.3)
                )
            self.assertTrue(self.session.send_touch(0, 0.9, 0.8))
            await asyncio.sleep(0)

        self.loop.run_until_complete(submit())
        native_queue = self.session._control_queue
        rfb_queue = self.session._rfb_touch_queue
        assert native_queue is not None
        assert rfb_queue is not None
        self.assertEqual(0, native_queue.qsize())
        queued = [rfb_queue.get_nowait() for _ in range(rfb_queue.qsize())]
        self.assertEqual(
            ["rfb_pointer", "rfb_move_latest", "rfb_pointer"],
            [item.kind for item in queued],
        )
        self.assertEqual(1, queued[0].value.phase)
        self.assertEqual(0, queued[-1].value.phase)
        generation = queued[1].value
        self.assertIsInstance(generation, int)
        self.assertEqual(
            PointerSample(2, 0.9, 0.8),
            self.session._rfb_latest_moves[generation],
        )

    def test_cancel_and_offline_touch_are_rejected(self) -> None:
        self.assertFalse(self.session.send_touch(3, 0.4, 0.6))
        self.session._set_touch_online(False)
        self.assertFalse(self.session.send_touch(1, 0.4, 0.6))

    def test_latest_marker_is_cleared_before_usb_write(self) -> None:
        class Client:
            def __init__(self) -> None:
                self.moves: list[tuple[float, float]] = []

            async def pointer_move(self, x: float, y: float) -> None:
                self.moves.append((x, y))

        async def send() -> Client:
            client = Client()
            self.session._rfb_latest_moves[7] = PointerSample(2, 0.4, 0.6)
            self.session._rfb_move_markers.add(7)
            await self.session._send_latest_rfb_move(client, 7)
            return client

        client = self.loop.run_until_complete(send())
        self.assertEqual([(0.4, 0.6)], client.moves)
        self.assertNotIn(7, self.session._rfb_latest_moves)
        self.assertNotIn(7, self.session._rfb_move_markers)


class TouchCoordinateTests(unittest.TestCase):
    class Owner:
        def __init__(self) -> None:
            self.touches: list[tuple[object, int, float, float, bool]] = []
            self.ime_activations: list[dict[str, object]] = []

        def route_touch(
            self,
            session: object,
            kind: int,
            x: float,
            y: float,
            *,
            from_master: bool,
        ) -> None:
            self.touches.append((session, kind, x, y, from_master))

        def activate_ime(self, session: object, **kwargs: object) -> None:
            self.ime_activations.append({"session": session, **kwargs})

        @staticmethod
        def device_label(udid: str) -> str:
            return udid

    def _view(self, view_type: type[DeviceTile] | type[MasterView]):
        view = view_type.__new__(view_type)
        view.owner = self.Owner()
        view.session = SimpleNamespace(udid="test-touch-device")
        view.image_bounds = (10, 20, 110, 220)
        view.dragging = False
        view.last_touch_point = None
        return view

    def test_regular_move_outside_image_is_ignored(self) -> None:
        event = SimpleNamespace(x=150, y=100)
        for view_type in (DeviceTile, MasterView):
            view = view_type.__new__(view_type)
            view.image_bounds = (10, 20, 110, 220)
            self.assertIsNone(view._normalized(event))

    def test_all_mouse_moves_reach_session_scheduler_and_release_is_raw(self) -> None:
        for view_type in (DeviceTile, MasterView):
            with self.subTest(view=view_type.__name__):
                view = self._view(view_type)
                from_master = view_type is MasterView
                view._press(SimpleNamespace(x=20, y=40, x_root=120, y_root=140))
                view._move(SimpleNamespace(x=40, y=80, x_root=140, y_root=180))
                view._move(SimpleNamespace(x=90, y=180, x_root=190, y_root=280))
                view._release(SimpleNamespace(x=100, y=200, x_root=200, y_root=300))

                self.assertEqual(
                    [
                        (view.session, 1, 0.1, 0.1, from_master),
                        (view.session, 2, 0.3, 0.3, from_master),
                        (view.session, 2, 0.8, 0.8, from_master),
                        (view.session, 0, 0.9, 0.9, from_master),
                    ],
                    view.owner.touches,
                )
                self.assertFalse(view.dragging)
                self.assertIsNone(view.last_touch_point)

    def test_release_outside_canvas_uses_last_valid_coordinate(self) -> None:
        for view_type in (DeviceTile, MasterView):
            with self.subTest(view=view_type.__name__):
                view = self._view(view_type)
                from_master = view_type is MasterView
                view._press(SimpleNamespace(x=30, y=60, x_root=130, y_root=160))
                view._move(SimpleNamespace(x=60, y=120, x_root=160, y_root=220))
                view._release(SimpleNamespace(x=500, y=-200, x_root=600, y_root=-100))

                self.assertEqual(
                    [
                        (view.session, 1, 0.2, 0.2, from_master),
                        (view.session, 2, 0.5, 0.5, from_master),
                        (view.session, 0, 0.5, 0.5, from_master),
                    ],
                    view.owner.touches,
                )

    def test_master_switch_finishes_old_drag_before_replacing_session(self) -> None:
        class Widget:
            def configure(self, **_kwargs: object) -> None:
                pass

        view = self._view(MasterView)
        old_session = view.session
        new_session = SimpleNamespace(udid="new-master")
        view.title = Widget()
        view.status = Widget()
        view.dragging = True
        view.last_touch_point = (0.4, 0.6)

        view.set_session(new_session)

        self.assertEqual(
            [(old_session, 0, 0.4, 0.6, True)],
            view.owner.touches,
        )
        self.assertIs(view.session, new_session)
        self.assertFalse(view.dragging)
        self.assertIsNone(view.last_touch_point)


if __name__ == "__main__":
    unittest.main()
