from __future__ import annotations

import argparse
import ctypes
import json
import sys
import tkinter as tk
from typing import Any

from xinglan.ime_position import place_ime_caret


IME_WINDOW_ALPHA = 0.0
ANCHOR_POLL_MS = 250
CFS_FORCE_POSITION = 0x0020
CFS_CANDIDATEPOS = 0x0040
EVENT_OBJECT_DESTROY = 0x8001
EVENT_OBJECT_SHOW = 0x8002
EVENT_OBJECT_LOCATIONCHANGE = 0x800B
OBJID_WINDOW = 0
OBJID_CLIENT = -4
WINEVENT_OUTOFCONTEXT = 0x0000
SWP_NOSIZE_NOZORDER_NOACTIVATE = 0x0015


class POINT(ctypes.Structure):
    _fields_ = [("x", ctypes.c_long), ("y", ctypes.c_long)]


class RECT(ctypes.Structure):
    _fields_ = [
        ("left", ctypes.c_long),
        ("top", ctypes.c_long),
        ("right", ctypes.c_long),
        ("bottom", ctypes.c_long),
    ]


class COMPOSITIONFORM(ctypes.Structure):
    _fields_ = [
        ("dwStyle", ctypes.c_uint32),
        ("ptCurrentPos", POINT),
        ("rcArea", RECT),
    ]


class CANDIDATEFORM(ctypes.Structure):
    _fields_ = [
        ("dwIndex", ctypes.c_uint32),
        ("dwStyle", ctypes.c_uint32),
        ("ptCurrentPos", POINT),
        ("rcArea", RECT),
    ]


def is_stuck_top_left_candidate(rect: RECT) -> bool:
    """Recognize the compact IME panel shown at the RDP desktop origin."""

    width = int(rect.right - rect.left)
    height = int(rect.bottom - rect.top)
    return (
        -8 <= int(rect.left) <= 100
        and -8 <= int(rect.top) <= 180
        and 80 <= width <= 900
        and 20 <= height <= 320
    )


def visible_window_handles(user32: Any) -> set[int]:
    """Snapshot visible top-level HWNDs without retaining callback pointers."""

    handles: set[int] = set()
    callback_type = ctypes.WINFUNCTYPE(ctypes.c_bool, ctypes.c_void_p, ctypes.c_void_p)

    @callback_type
    def collect(hwnd: int, _lparam: int) -> bool:
        if user32.IsWindowVisible(ctypes.c_void_p(hwnd)):
            handles.add(int(hwnd))
        return True

    user32.EnumWindows.argtypes = [callback_type, ctypes.c_void_p]
    user32.EnumWindows(collect, None)
    return handles


def candidate_target_in_owner(owner: RECT, candidate: RECT) -> tuple[int, int]:
    """Place the candidate panel inside the Xinglan window's bottom-right."""

    width = max(1, int(candidate.right - candidate.left))
    height = max(1, int(candidate.bottom - candidate.top))
    target_x = int(owner.right) - width - 20
    target_y = int(owner.bottom) - height - 60
    return max(int(owner.left), target_x), max(int(owner.top), target_y)


def root_window_handle(user32: Any, handle: int) -> int:
    """Normalize child/object HWNDs from WinEvent to their top-level window."""

    if not handle:
        return 0
    try:
        user32.GetAncestor.argtypes = [ctypes.c_void_p, ctypes.c_uint]
        user32.GetAncestor.restype = ctypes.c_void_p
        ancestor = user32.GetAncestor(ctypes.c_void_p(handle), 2)  # GA_ROOT
        if ancestor:
            return int(ancestor)
    except (AttributeError, OSError, ValueError, ctypes.ArgumentError):
        pass
    return int(handle)


class CandidateWindowPinner:
    """Pin a remote-session IME candidate panel to the Xinglan window."""

    _callback_type = ctypes.WINFUNCTYPE(
        None,
        ctypes.c_void_p,
        ctypes.c_uint,
        ctypes.c_void_p,
        ctypes.c_long,
        ctypes.c_long,
        ctypes.c_uint,
        ctypes.c_uint,
    )

    def __init__(
        self,
        user32: Any,
        owner_hwnd: int,
        baseline_visible: set[int],
        excluded_handles: set[int],
    ) -> None:
        self.user32 = user32
        self.owner_hwnd = root_window_handle(user32, owner_hwnd)
        self.baseline_visible = {int(handle) for handle in baseline_visible}
        self.excluded_handles = {int(handle) for handle in excluded_handles}
        self.pinned_handles: set[int] = set()
        self._moving_handles: set[int] = set()
        self._hooks: list[int] = []
        self._moved_events: list[tuple[int, int, int]] = []
        self._callback = self._callback_type(self._on_win_event)
        self._configure_api()

    def _configure_api(self) -> None:
        self.user32.GetWindowRect.argtypes = [ctypes.c_void_p, ctypes.POINTER(RECT)]
        self.user32.SetWindowPos.argtypes = [
            ctypes.c_void_p,
            ctypes.c_void_p,
            ctypes.c_int,
            ctypes.c_int,
            ctypes.c_int,
            ctypes.c_int,
            ctypes.c_uint,
        ]
        self.user32.SetWinEventHook.argtypes = [
            ctypes.c_uint,
            ctypes.c_uint,
            ctypes.c_void_p,
            self._callback_type,
            ctypes.c_uint,
            ctypes.c_uint,
            ctypes.c_uint,
        ]
        self.user32.SetWinEventHook.restype = ctypes.c_void_p

    def start(self) -> bool:
        for event in (
            EVENT_OBJECT_DESTROY,
            EVENT_OBJECT_SHOW,
            EVENT_OBJECT_LOCATIONCHANGE,
        ):
            hook = self.user32.SetWinEventHook(
                event,
                event,
                None,
                self._callback,
                0,
                0,
                WINEVENT_OUTOFCONTEXT,
            )
            if hook:
                self._hooks.append(int(hook))
        return bool(self._hooks)

    def close(self) -> None:
        try:
            self.user32.UnhookWinEvent.argtypes = [ctypes.c_void_p]
            for hook in self._hooks:
                self.user32.UnhookWinEvent(ctypes.c_void_p(hook))
        except (AttributeError, OSError, ValueError, ctypes.ArgumentError):
            pass
        self._hooks.clear()
        self.pinned_handles.clear()

    def add_excluded_handle(self, handle: int) -> None:
        normalized = root_window_handle(self.user32, handle)
        if normalized:
            self.excluded_handles.add(normalized)

    def _read_rect(self, handle: int) -> RECT | None:
        rect = RECT()
        if not self.user32.GetWindowRect(ctypes.c_void_p(handle), ctypes.byref(rect)):
            return None
        return rect

    def _is_candidate(self, handle: int) -> bool:
        if (
            not handle
            or handle == self.owner_hwnd
            or handle in self.baseline_visible
            or handle in self.excluded_handles
        ):
            return False
        try:
            if not self.user32.IsWindowVisible(ctypes.c_void_p(handle)):
                return False
            rect = self._read_rect(handle)
            return rect is not None and is_stuck_top_left_candidate(rect)
        except (AttributeError, OSError, ValueError, ctypes.ArgumentError):
            return False

    def _pin_or_enforce(self, handle: int) -> bool:
        if handle not in self.pinned_handles:
            if not self._is_candidate(handle):
                return False
            self.pinned_handles.add(handle)
        return self._enforce(handle)

    def _enforce(self, handle: int) -> bool:
        if handle in self._moving_handles or not self.owner_hwnd:
            return False
        try:
            if not self.user32.IsWindow(ctypes.c_void_p(handle)):
                self.pinned_handles.discard(handle)
                return False
            candidate_rect = self._read_rect(handle)
            owner_rect = self._read_rect(self.owner_hwnd)
            if candidate_rect is None or owner_rect is None:
                return False
            target_x, target_y = candidate_target_in_owner(owner_rect, candidate_rect)
            if (
                abs(int(candidate_rect.left) - target_x) <= 1
                and abs(int(candidate_rect.top) - target_y) <= 1
            ):
                return False
            self._moving_handles.add(handle)
            try:
                moved = bool(
                    self.user32.SetWindowPos(
                        ctypes.c_void_p(handle),
                        None,
                        target_x,
                        target_y,
                        0,
                        0,
                        SWP_NOSIZE_NOZORDER_NOACTIVATE,
                    )
                )
            finally:
                self._moving_handles.discard(handle)
            if moved:
                self._moved_events.append((handle, target_x, target_y))
            return moved
        except (AttributeError, OSError, ValueError, ctypes.ArgumentError):
            return False

    def _on_win_event(
        self,
        _hook: int,
        event: int,
        hwnd: int,
        object_id: int,
        _child_id: int,
        _thread_id: int,
        _event_time: int,
    ) -> None:
        if object_id not in (OBJID_WINDOW, OBJID_CLIENT) or not hwnd:
            return
        handle = root_window_handle(self.user32, int(hwnd))
        if event == EVENT_OBJECT_DESTROY:
            self.pinned_handles.discard(handle)
            return
        if handle == self.owner_hwnd:
            if event == EVENT_OBJECT_LOCATIONCHANGE:
                for pinned in tuple(self.pinned_handles):
                    self._enforce(pinned)
            return
        if event in (EVENT_OBJECT_SHOW, EVENT_OBJECT_LOCATIONCHANGE):
            self._pin_or_enforce(handle)

    def reconcile(self) -> None:
        """Recover missed events; normal positioning is callback-driven."""

        try:
            current = visible_window_handles(self.user32)
            for handle in tuple(self.pinned_handles):
                if handle not in current:
                    self.pinned_handles.discard(handle)
                else:
                    self._enforce(handle)
            for handle in current:
                self._pin_or_enforce(root_window_handle(self.user32, handle))
        except (AttributeError, OSError, ValueError, ctypes.ArgumentError):
            return

    def drain_moved_events(self) -> list[tuple[int, int, int]]:
        events = self._moved_events[:]
        self._moved_events.clear()
        return events


def emit(kind: str, *values: Any) -> None:
    stream = sys.stdout
    if stream is None:
        return
    # Keep the pipe ASCII-only because a detached Windows process may inherit
    # GBK. json.loads restores the original Unicode text in the main process.
    stream.write(json.dumps([kind, *values], ensure_ascii=True) + "\n")
    stream.flush()


def resolve_screen_anchor(
    user32: Any,
    fallback_x: int,
    fallback_y: int,
    owner_hwnd: int,
    anchor_x: int,
    anchor_y: int,
) -> tuple[int, int]:
    """Resolve a main-window client point into signed virtual-screen pixels."""

    if not owner_hwnd:
        return int(fallback_x), int(fallback_y)
    point = POINT(int(anchor_x), int(anchor_y))
    try:
        user32.ClientToScreen.argtypes = [ctypes.c_void_p, ctypes.POINTER(POINT)]
        if user32.ClientToScreen(ctypes.c_void_p(owner_hwnd), ctypes.byref(point)):
            return int(point.x), int(point.y)
    except (AttributeError, OSError, ValueError, ctypes.ArgumentError):
        pass
    return int(fallback_x), int(fallback_y)


def apply_native_ime_anchor(root: Any, entry: Any) -> bool:
    """Force both IMM composition and candidate UI beside the hidden caret."""

    try:
        imm32 = ctypes.windll.imm32
        imm32.ImmGetContext.argtypes = [ctypes.c_void_p]
        imm32.ImmGetContext.restype = ctypes.c_void_p
        imm32.ImmReleaseContext.argtypes = [ctypes.c_void_p, ctypes.c_void_p]
        imm32.ImmSetCompositionWindow.argtypes = [
            ctypes.c_void_p,
            ctypes.POINTER(COMPOSITIONFORM),
        ]
        imm32.ImmSetCandidateWindow.argtypes = [
            ctypes.c_void_p,
            ctypes.POINTER(CANDIDATEFORM),
        ]
    except (AttributeError, OSError, ValueError, ctypes.ArgumentError):
        return False

    composition = COMPOSITIONFORM(
        CFS_FORCE_POSITION,
        POINT(0, 0),
        RECT(0, 0, 2, 24),
    )
    candidate = CANDIDATEFORM(
        0,
        CFS_CANDIDATEPOS,
        POINT(0, 24),
        RECT(0, 0, 2, 24),
    )
    applied = False
    handles: list[int] = []
    try:
        user32 = ctypes.windll.user32
        user32.GetFocus.restype = ctypes.c_void_p
        focused_handle = user32.GetFocus()
        if focused_handle:
            handles.append(int(focused_handle))
    except (AttributeError, OSError, ValueError, ctypes.ArgumentError):
        pass
    for widget in (entry, root):
        try:
            handle = int(widget.winfo_id())
        except (AttributeError, TypeError, ValueError, tk.TclError):
            continue
        if handle and handle not in handles:
            handles.append(handle)

    for handle in handles:
        native_handle = ctypes.c_void_p(handle)
        context = imm32.ImmGetContext(native_handle)
        if not context:
            continue
        try:
            composition_ok = bool(
                imm32.ImmSetCompositionWindow(context, ctypes.byref(composition))
            )
            candidate_ok = bool(
                imm32.ImmSetCandidateWindow(context, ctypes.byref(candidate))
            )
            applied = applied or composition_ok or candidate_ok
        finally:
            imm32.ImmReleaseContext(native_handle, context)
    return applied


def top_level_window_handle(root: Any, user32: Any) -> int:
    """Return the actual Windows top-level HWND for a Tk root."""

    inner_handle = int(root.winfo_id())
    try:
        user32.GetAncestor.argtypes = [ctypes.c_void_p, ctypes.c_uint]
        user32.GetAncestor.restype = ctypes.c_void_p
        ancestor = user32.GetAncestor(ctypes.c_void_p(inner_handle), 2)  # GA_ROOT
        if ancestor:
            return int(ancestor)
    except (AttributeError, OSError, ValueError, ctypes.ArgumentError):
        pass
    return inner_handle


def create_native_caret(user32: Any) -> bool:
    """Publish a real Win32 caret for TSF/RDP input-method positioning."""

    try:
        user32.GetFocus.restype = ctypes.c_void_p
        focused = user32.GetFocus()
        if not focused:
            return False
        focused_hwnd = ctypes.c_void_p(focused)
        user32.CreateCaret.argtypes = [
            ctypes.c_void_p,
            ctypes.c_void_p,
            ctypes.c_int,
            ctypes.c_int,
        ]
        user32.SetCaretPos.argtypes = [ctypes.c_int, ctypes.c_int]
        user32.ShowCaret.argtypes = [ctypes.c_void_p]
        user32.DestroyCaret()
        if not user32.CreateCaret(focused_hwnd, None, 2, 24):
            return False
        user32.SetCaretPos(0, 0)
        user32.ShowCaret(focused_hwnd)
        return True
    except (AttributeError, OSError, ValueError, ctypes.ArgumentError):
        return False


def run_worker(
    screen_x: int,
    screen_y: int,
    owner_hwnd: int = 0,
    anchor_x: int = 0,
    anchor_y: int = 0,
) -> None:
    """Run a tiny, disposable Windows IME owner.

    This is the last live-verified input path: the helper owns Windows IME
    composition memory while projection, USB and grouping remain in the
    long-running main process.  Its owner terminates it when input focus moves.
    """
    root = tk.Tk()
    root.withdraw()
    root.overrideredirect(True)
    root.attributes("-topmost", True)
    # The helper must own keyboard focus so Windows IME can compose text, but
    # its 2x2 native window must never be visible over the projected phone.
    root.attributes("-alpha", IME_WINDOW_ALPHA)
    # Start harmlessly on the primary display.  force_focus() places the
    # native top-level with signed virtual-screen coordinates; Tk's geometry
    # syntax interprets negative values as offsets from the right/bottom edge
    # and therefore cannot represent a monitor to the left of the primary.
    root.geometry("2x2+0+0")

    value = tk.StringVar(value="")
    consuming = False
    consume_job: str | None = None
    entry = tk.Entry(
        root,
        textvariable=value,
        borderwidth=0,
        highlightthickness=0,
        takefocus=True,
        cursor="arrow",
    )
    entry.place(x=0, y=0, width=2, height=2)
    last_anchor: tuple[int, int] | None = None
    try:
        candidate_baseline = visible_window_handles(ctypes.windll.user32)
    except (AttributeError, OSError, ValueError, ctypes.ArgumentError):
        candidate_baseline = set()
    candidate_exclusions: set[int] = {int(owner_hwnd)} if owner_hwnd else set()
    candidate_pinner: CandidateWindowPinner | None = None

    def note_activity(_event: tk.Event | None = None) -> None:
        # The owning ImeWorkerClient terminates this disposable process when
        # the user clicks another phone, stops projection, switches groups, or
        # closes the app.  Do not add an idle self-exit here: once this hidden
        # window owns keyboard focus, exiting leaves neither it nor the main
        # canvas able to receive typing/Ctrl+V until the phone is clicked again.
        return None

    def consume() -> None:
        nonlocal consuming, consume_job
        consume_job = None
        text = value.get()
        if not text:
            return
        consuming = True
        try:
            value.set("")
        finally:
            consuming = False
        note_activity()
        emit("text", text)

    def changed(*_args: object) -> None:
        nonlocal consume_job
        if consuming:
            return
        if consume_job is None:
            consume_job = root.after_idle(consume)

    def send_key(page: int, usage: int, label: str) -> str:
        note_activity()
        emit("key", page, usage, label)
        return "break"

    def paste(_event: tk.Event | None = None) -> str:
        try:
            text = root.clipboard_get()
        except tk.TclError:
            text = ""
        note_activity()
        if text:
            emit("text", text)
        return "break"

    def position_helper(*, force_ime: bool = False) -> None:
        nonlocal last_anchor
        try:
            user32 = ctypes.windll.user32
            resolved = resolve_screen_anchor(
                user32,
                screen_x,
                screen_y,
                owner_hwnd,
                anchor_x,
                anchor_y,
            )
            hwnd = top_level_window_handle(root, user32)
            if resolved != last_anchor:
                user32.SetWindowPos.argtypes = [
                    ctypes.c_void_p,
                    ctypes.c_void_p,
                    ctypes.c_int,
                    ctypes.c_int,
                    ctypes.c_int,
                    ctypes.c_int,
                    ctypes.c_uint,
                ]
                user32.SetWindowPos(
                    ctypes.c_void_p(hwnd),
                    ctypes.c_void_p(-1),
                    resolved[0],
                    resolved[1],
                    2,
                    2,
                    0x0040,
                )
                last_anchor = resolved
                force_ime = True
            if force_ime:
                root.update_idletasks()
                place_ime_caret(entry, 0, 0, height=24)
                create_native_caret(user32)
                apply_native_ime_anchor(root, entry)
        except (AttributeError, OSError, ValueError, ctypes.ArgumentError, tk.TclError):
            pass

    def track_anchor() -> None:
        nonlocal candidate_pinner
        position_helper()
        try:
            user32 = ctypes.windll.user32
            helper_handle = top_level_window_handle(root, user32)
            candidate_exclusions.add(helper_handle)
            if candidate_pinner is None:
                candidate_pinner = CandidateWindowPinner(
                    user32,
                    owner_hwnd,
                    candidate_baseline,
                    candidate_exclusions,
                )
                candidate_pinner.add_excluded_handle(helper_handle)
                candidate_pinner.start()
            else:
                candidate_pinner.add_excluded_handle(helper_handle)
            candidate_pinner.reconcile()
            for moved_handle, target_x, target_y in candidate_pinner.drain_moved_events():
                emit("candidate_moved", moved_handle, target_x, target_y)
        except (AttributeError, OSError, ValueError, ctypes.ArgumentError, tk.TclError):
            pass
        root.after(ANCHOR_POLL_MS, track_anchor)

    def force_focus() -> None:
        root.deiconify()
        root.lift()
        root.update_idletasks()
        position_helper(force_ime=True)
        try:
            user32 = ctypes.windll.user32
            hwnd = top_level_window_handle(root, user32)
            native_hwnd = ctypes.c_void_p(hwnd)
            user32.ShowWindow(native_hwnd, 5)
            user32.SetForegroundWindow(native_hwnd)
            user32.SetActiveWindow(native_hwnd)
            user32.SetFocus(native_hwnd)
        except (AttributeError, OSError, ValueError):
            pass
        root.focus_force()
        entry.focus_force()
        try:
            create_native_caret(ctypes.windll.user32)
        except (AttributeError, OSError):
            pass
        position_helper(force_ime=True)
        note_activity()
        emit("ready")

    value.trace_add("write", changed)
    entry.bind("<KeyPress>", note_activity, add="+")
    entry.bind("<Control-v>", paste)
    entry.bind("<Control-V>", paste)
    entry.bind("<Return>", lambda _event: send_key(0x07, 0x28, "回车"))
    entry.bind("<KP_Enter>", lambda _event: send_key(0x07, 0x28, "回车"))
    entry.bind("<BackSpace>", lambda _event: send_key(0x07, 0x2A, "退格"))
    # Install the event hook before the helper takes focus.  Otherwise the
    # first candidate window can be painted at (0, 0) before the fallback
    # maintenance pass has even started.
    try:
        user32 = ctypes.windll.user32
        helper_handle = top_level_window_handle(root, user32)
        candidate_exclusions.add(helper_handle)
        candidate_pinner = CandidateWindowPinner(
            user32,
            owner_hwnd,
            candidate_baseline,
            candidate_exclusions,
        )
        candidate_pinner.add_excluded_handle(helper_handle)
        candidate_pinner.start()
    except (AttributeError, OSError, ValueError, ctypes.ArgumentError, tk.TclError):
        candidate_pinner = None
    root.after(20, force_focus)
    root.after(90, force_focus)
    root.after(ANCHOR_POLL_MS, track_anchor)
    root.mainloop()


def main() -> None:
    parser = argparse.ArgumentParser(add_help=False)
    parser.add_argument("--x", type=int, required=True)
    parser.add_argument("--y", type=int, required=True)
    parser.add_argument("--owner-hwnd", type=int, default=0)
    parser.add_argument("--anchor-x", type=int, default=0)
    parser.add_argument("--anchor-y", type=int, default=0)
    args = parser.parse_args()
    run_worker(
        args.x,
        args.y,
        args.owner_hwnd,
        args.anchor_x,
        args.anchor_y,
    )


if __name__ == "__main__":
    main()
