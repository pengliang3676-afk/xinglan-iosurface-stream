from __future__ import annotations

from dataclasses import dataclass
from typing import Callable, Protocol


TOUCH_UP = 0
TOUCH_DOWN = 1
TOUCH_MOVE = 2
NOVNC_MOVE_INTERVAL_SECONDS = 0.017


@dataclass(frozen=True)
class PointerSample:
    phase: int
    x: float
    y: float


class _TimerHandle(Protocol):
    def cancel(self) -> None: ...


class _SchedulerLoop(Protocol):
    def time(self) -> float: ...

    def call_later(
        self,
        delay: float,
        callback: Callable[[], None],
    ) -> _TimerHandle: ...


class NoVncPointerScheduler:
    """Reproduce noVNC's trailing-latest 17 ms pointer scheduling.

    All methods must run on the owning asyncio loop.  MOVE events arriving
    inside the current 17 ms window replace the pending coordinate instead of
    being discarded.  Releasing while a MOVE timer is pending first flushes a
    pressed MOVE at the release coordinate, then emits UP at that same point.
    """

    def __init__(
        self,
        loop: _SchedulerLoop,
        emit: Callable[[PointerSample], None],
        *,
        move_interval: float = NOVNC_MOVE_INTERVAL_SECONDS,
    ) -> None:
        if move_interval <= 0:
            raise ValueError("move_interval must be positive")
        self._loop = loop
        self._emit = emit
        self._move_interval = move_interval
        self._pressed = False
        self._latest_point: tuple[float, float] | None = None
        self._last_move_sent_at: float | None = None
        self._move_timer: _TimerHandle | None = None

    @property
    def pressed(self) -> bool:
        return self._pressed

    @property
    def has_pending_move(self) -> bool:
        return self._move_timer is not None

    def submit(self, phase: int, x: float, y: float) -> None:
        point = (float(x), float(y))
        if phase == TOUCH_DOWN:
            self._submit_down(point)
        elif phase == TOUCH_MOVE:
            self._submit_move(point)
        elif phase == TOUCH_UP:
            self._submit_up(point)
        else:
            raise ValueError(f"unsupported TrollVNC pointer phase: {phase}")

    def reset(self) -> None:
        """Forget local state without emitting into a failed connection."""

        self._cancel_move_timer()
        self._pressed = False
        self._latest_point = None
        self._last_move_sent_at = None

    def _submit_down(self, point: tuple[float, float]) -> None:
        self._cancel_move_timer()
        if self._pressed and self._latest_point is not None:
            # Defensive recovery from a duplicate DOWN: close the old contact
            # before beginning a new RFB button lifecycle.
            self._emit_sample(TOUCH_UP, self._latest_point)
        self._pressed = True
        self._latest_point = point
        # noVNC allows the first held MOVE through immediately.
        self._last_move_sent_at = None
        self._emit_sample(TOUCH_DOWN, point)

    def _submit_move(self, point: tuple[float, float]) -> None:
        if not self._pressed:
            return
        self._latest_point = point
        if self._move_timer is not None:
            return

        now = self._loop.time()
        if self._last_move_sent_at is None:
            self._emit_sample(TOUCH_MOVE, point)
            self._last_move_sent_at = now
            return
        elapsed = now - self._last_move_sent_at
        if elapsed >= self._move_interval:
            self._emit_sample(TOUCH_MOVE, point)
            self._last_move_sent_at = now
            return

        self._move_timer = self._loop.call_later(
            self._move_interval - elapsed,
            self._flush_delayed_move,
        )

    def _submit_up(self, point: tuple[float, float]) -> None:
        if not self._pressed:
            return
        self._latest_point = point
        if self._move_timer is not None:
            # This is the important noVNC ordering: pending pressed MOVE first,
            # then UP at the exact same release coordinate.
            self._cancel_move_timer()
            self._emit_sample(TOUCH_MOVE, point)
            self._last_move_sent_at = self._loop.time()
        self._pressed = False
        self._emit_sample(TOUCH_UP, point)

    def _flush_delayed_move(self) -> None:
        self._move_timer = None
        if not self._pressed or self._latest_point is None:
            return
        self._emit_sample(TOUCH_MOVE, self._latest_point)
        self._last_move_sent_at = self._loop.time()

    def _cancel_move_timer(self) -> None:
        timer = self._move_timer
        self._move_timer = None
        if timer is not None:
            timer.cancel()

    def _emit_sample(self, phase: int, point: tuple[float, float]) -> None:
        self._emit(PointerSample(phase, point[0], point[1]))
