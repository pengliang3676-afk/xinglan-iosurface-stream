from __future__ import annotations

import asyncio
import sys
import unittest
from pathlib import Path


PROJECT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT))

from xinglan.control_protocol import TouchCommand, TouchPhase
from xinglan.session import DeviceSession


class TouchTransportTests(unittest.TestCase):
    def setUp(self) -> None:
        self.loop = asyncio.new_event_loop()
        self.session = DeviceSession("test-touch-device")
        async def initialize() -> None:
            self.session._loop = asyncio.get_running_loop()
            self.session._control_queue = asyncio.Queue(maxsize=64)

        self.loop.run_until_complete(initialize())

    def tearDown(self) -> None:
        self.loop.close()

    def _drain_callbacks(self) -> None:
        self.loop.run_until_complete(asyncio.sleep(0))

    def test_many_moves_keep_one_queue_marker_and_latest_coordinate(self) -> None:
        async def submit() -> None:
            for index in range(100):
                self.assertTrue(
                    self.session.send_touch(2, index / 100.0, index / 200.0)
                )
            await asyncio.sleep(0)

        self.loop.run_until_complete(submit())

        queue = self.session._control_queue
        assert queue is not None
        self.assertEqual(1, queue.qsize())
        self.assertEqual("touch_move_latest", queue.get_nowait().kind)
        latest = self.session._latest_touch_move
        self.assertIsNotNone(latest)
        assert latest is not None
        _, command = latest
        self.assertIsInstance(command, TouchCommand)
        self.assertAlmostEqual(0.99, command.x)
        self.assertAlmostEqual(0.495, command.y)

    def test_down_move_up_order_has_no_move_fifo(self) -> None:
        async def submit() -> None:
            self.assertTrue(self.session.send_touch(1, 0.1, 0.2))
            for index in range(50):
                self.assertTrue(
                    self.session.send_touch(2, 0.2 + index / 100.0, 0.3)
                )
            self.assertTrue(self.session.send_touch(0, 0.7, 0.3))
            await asyncio.sleep(0)

        self.loop.run_until_complete(submit())

        queue = self.session._control_queue
        assert queue is not None
        queued = [queue.get_nowait() for _ in range(queue.qsize())]
        self.assertEqual(
            ["touch", "touch_move_latest", "touch"],
            [envelope.kind for envelope in queued],
        )
        self.assertEqual(TouchPhase.DOWN, queued[0].value.phase)
        self.assertEqual(TouchPhase.UP, queued[-1].value.phase)
        self.assertIsNone(self.session._latest_touch_move)

    def test_delayed_old_marker_cannot_steal_next_drag_coordinate(self) -> None:
        async def submit() -> None:
            self.session.send_touch(1, 0.1, 0.1)
            self.session.send_touch(2, 0.2, 0.2)
            self.session.send_touch(0, 0.3, 0.3)
            self.session.send_touch(1, 0.4, 0.4)
            self.session.send_touch(2, 0.5, 0.5)
            await asyncio.sleep(0)

        self.loop.run_until_complete(submit())
        queue = self.session._control_queue
        assert queue is not None
        queued = [queue.get_nowait() for _ in range(queue.qsize())]
        markers = [item for item in queued if item.kind == "touch_move_latest"]
        self.assertEqual([1, 2], [item.value for item in markers])
        self.assertEqual(2, self.session._latest_touch_move[0])


if __name__ == "__main__":
    unittest.main()
