from __future__ import annotations

import argparse
import ctypes
import json
import sys
import tkinter as tk
from typing import Any

from xinglan.ime_position import place_ime_caret


IME_WINDOW_ALPHA = 0.0


def emit(kind: str, *values: Any) -> None:
    stream = sys.stdout
    if stream is None:
        return
    # Keep the pipe ASCII-only because a detached Windows process may inherit
    # GBK. json.loads restores the original Unicode text in the main process.
    stream.write(json.dumps([kind, *values], ensure_ascii=True) + "\n")
    stream.flush()


def run_worker(screen_x: int, screen_y: int) -> None:
    """Run a tiny, disposable Windows IME owner.

    This is the last live-verified input path: the helper owns Windows IME
    composition memory and exits after an idle input burst, while projection,
    USB and grouping remain in the long-running main process.
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

    def force_focus() -> None:
        root.deiconify()
        root.lift()
        root.update_idletasks()
        try:
            user32 = ctypes.windll.user32
            frame_id = root.frame()
            hwnd = int(str(frame_id), 0) if frame_id else int(root.winfo_id())
            # SetWindowPos accepts signed virtual-screen coordinates, so an
            # IME opened on a monitor left/above the primary screen remains
            # next to the projected phone instead of being clamped to (1, 1).
            user32.SetWindowPos.argtypes = [
                ctypes.c_void_p,
                ctypes.c_void_p,
                ctypes.c_int,
                ctypes.c_int,
                ctypes.c_int,
                ctypes.c_int,
                ctypes.c_uint,
            ]
            native_hwnd = ctypes.c_void_p(hwnd)
            user32.SetWindowPos(
                native_hwnd,
                ctypes.c_void_p(-1),
                int(screen_x),
                int(screen_y),
                2,
                2,
                0x0040,
            )
            user32.ShowWindow(native_hwnd, 5)
            user32.SetForegroundWindow(native_hwnd)
            user32.SetActiveWindow(native_hwnd)
            user32.SetFocus(native_hwnd)
        except (AttributeError, OSError, ValueError):
            pass
        root.focus_force()
        entry.focus_force()
        # Tk otherwise reports a default caret at the application's origin;
        # several Windows IMEs then pin their composition/candidate window to
        # the upper-left corner even though the hidden helper itself was moved.
        place_ime_caret(entry, 0, 0, height=24)
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
    root.mainloop()


def main() -> None:
    parser = argparse.ArgumentParser(add_help=False)
    parser.add_argument("--x", type=int, required=True)
    parser.add_argument("--y", type=int, required=True)
    args = parser.parse_args()
    run_worker(args.x, args.y)


if __name__ == "__main__":
    main()
