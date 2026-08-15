from __future__ import annotations

import argparse
import ctypes
import json
import sys
import tkinter as tk
from typing import Any

from xinglan.ime_position import place_ime_caret


IME_WINDOW_ALPHA = 0.0
ANCHOR_POLL_MS = 50
CFS_FORCE_POSITION = 0x0020
CFS_CANDIDATEPOS = 0x0040


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
            frame_id = root.frame()
            hwnd = int(str(frame_id), 0) if frame_id else int(root.winfo_id())
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
                apply_native_ime_anchor(root, entry)
        except (AttributeError, OSError, ValueError, ctypes.ArgumentError, tk.TclError):
            pass

    def track_anchor() -> None:
        position_helper()
        root.after(ANCHOR_POLL_MS, track_anchor)

    def force_focus() -> None:
        root.deiconify()
        root.lift()
        root.update_idletasks()
        position_helper(force_ime=True)
        try:
            user32 = ctypes.windll.user32
            frame_id = root.frame()
            hwnd = int(str(frame_id), 0) if frame_id else int(root.winfo_id())
            native_hwnd = ctypes.c_void_p(hwnd)
            user32.ShowWindow(native_hwnd, 5)
            user32.SetForegroundWindow(native_hwnd)
            user32.SetActiveWindow(native_hwnd)
            user32.SetFocus(native_hwnd)
        except (AttributeError, OSError, ValueError):
            pass
        root.focus_force()
        entry.focus_force()
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
