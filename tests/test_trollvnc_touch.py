from __future__ import annotations

import unittest
from collections.abc import Callable

from xinglan.trollvnc_touch import (
    NOVNC_MOVE_INTERVAL_SECONDS,
    TOUCH_DOWN,
    TOUCH_MOVE,
    TOUCH_UP,
    NoVncPointerScheduler,
    PointerSample,
)


class FakeHandle:
    def __init__(self, due: float, callback: Callable[[], None]) -> None:
        self.due = due
        self.callback = callback
        self.cancelled = False

    def cancel(self) -> None:
        self.cancelled = True


class FakeLoop:
    def __init__(self) -> None:
        self.now = 10.0
        self.handles: list[FakeHandle] = []

    def time(self) -> float:
        return self.now

    def call_later(self, delay: float, callback: Callable[[], None]) -> FakeHandle:
        handle = FakeHandle(self.now + delay, callback)
        self.handles.append(handle)
        return handle

    def advance(self, seconds: float) -> None:
        target = self.now + seconds
        while True:
            pending = [
                handle
                for handle in self.handles
                if not handle.cancelled and handle.due <= target
            ]
            if not pending:
                break
            handle = min(pending, key=lambda item: item.due)
            self.handles.remove(handle)
            self.now = handle.due
            handle.callback()
        self.now = target


class NoVncPointerSchedulerTests(unittest.TestCase):
    def setUp(self) -> None:
        self.loop = FakeLoop()
        self.samples: list[PointerSample] = []
        self.scheduler = NoVncPointerScheduler(self.loop, self.samples.append)

    def test_first_move_after_down_is_immediate(self) -> None:
        self.scheduler.submit(TOUCH_DOWN, 0.1, 0.2)
        self.scheduler.submit(TOUCH_MOVE, 0.2, 0.3)

        self.assertEqual(
            [
                PointerSample(TOUCH_DOWN, 0.1, 0.2),
                PointerSample(TOUCH_MOVE, 0.2, 0.3),
            ],
            self.samples,
        )

    def test_moves_inside_window_flush_only_latest_coordinate(self) -> None:
        self.scheduler.submit(TOUCH_DOWN, 0.1, 0.1)
        self.scheduler.submit(TOUCH_MOVE, 0.2, 0.2)
        self.loop.advance(0.004)
        self.scheduler.submit(TOUCH_MOVE, 0.3, 0.3)
        self.loop.advance(0.004)
        self.scheduler.submit(TOUCH_MOVE, 0.8, 0.7)

        self.assertTrue(self.scheduler.has_pending_move)
        self.assertEqual(2, len(self.samples))
        self.loop.advance(NOVNC_MOVE_INTERVAL_SECONDS)

        self.assertEqual(PointerSample(TOUCH_MOVE, 0.8, 0.7), self.samples[-1])
        self.assertEqual(3, len(self.samples))

    def test_release_with_pending_move_flushes_move_then_up_at_release_point(self) -> None:
        self.scheduler.submit(TOUCH_DOWN, 0.1, 0.1)
        self.scheduler.submit(TOUCH_MOVE, 0.2, 0.2)
        self.scheduler.submit(TOUCH_MOVE, 0.3, 0.3)
        self.assertTrue(self.scheduler.has_pending_move)

        self.scheduler.submit(TOUCH_UP, 0.9, 0.8)

        self.assertEqual(
            [
                PointerSample(TOUCH_MOVE, 0.9, 0.8),
                PointerSample(TOUCH_UP, 0.9, 0.8),
            ],
            self.samples[-2:],
        )
        self.loop.advance(1.0)
        self.assertEqual(4, len(self.samples))

    def test_release_without_pending_move_does_not_invent_extra_move(self) -> None:
        self.scheduler.submit(TOUCH_DOWN, 0.1, 0.1)
        self.scheduler.submit(TOUCH_UP, 0.4, 0.5)

        self.assertEqual(
            [
                PointerSample(TOUCH_DOWN, 0.1, 0.1),
                PointerSample(TOUCH_UP, 0.4, 0.5),
            ],
            self.samples,
        )

    def test_reset_cancels_delayed_move_without_emitting(self) -> None:
        self.scheduler.submit(TOUCH_DOWN, 0.1, 0.1)
        self.scheduler.submit(TOUCH_MOVE, 0.2, 0.2)
        self.scheduler.submit(TOUCH_MOVE, 0.3, 0.3)
        self.scheduler.reset()
        self.loop.advance(1.0)

        self.assertEqual(2, len(self.samples))
        self.assertFalse(self.scheduler.pressed)

    def test_duplicate_down_closes_old_contact_first(self) -> None:
        self.scheduler.submit(TOUCH_DOWN, 0.1, 0.1)
        self.scheduler.submit(TOUCH_DOWN, 0.7, 0.8)

        self.assertEqual(
            [
                PointerSample(TOUCH_DOWN, 0.1, 0.1),
                PointerSample(TOUCH_UP, 0.1, 0.1),
                PointerSample(TOUCH_DOWN, 0.7, 0.8),
            ],
            self.samples,
        )


if __name__ == "__main__":
    unittest.main()
