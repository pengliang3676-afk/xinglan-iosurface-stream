from __future__ import annotations

import argparse
import ctypes
import json
import sys
import tkinter as tk
from typing import Any


IDLE_EXIT_MS = 8000
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
    root.geometry(f"2x2+{max(1, screen_x)}+{max(1, screen_y)}")

    value = tk.StringVar(value="")
    consuming = False
    consume_job: str | None = None
    exit_job: str | None = None
    entry = tk.Entry(
        root,
        textvariable=value,
        borderwidth=0,
        highlightthickness=0,
        takefocus=True,
        cursor="arrow",
    )
    entry.place(x=0, y=0, width=2, height=2)

    def close_worker() -> None:
        try:
            root.destroy()
        except tk.TclError:
            pass

    def note_activity(_event: tk.Event | None = None) -> None:
        nonlocal exit_job
        if exit_job is not None:
            try:
                root.after_cancel(exit_job)
            except tk.TclError:
                pass
        exit_job = root.after(IDLE_EXIT_MS, close_worker)

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
            hwnd = int(root.winfo_id())
            user32 = ctypes.windll.user32
            user32.ShowWindow(hwnd, 5)
            user32.SetForegroundWindow(hwnd)
            user32.SetActiveWindow(hwnd)
            user32.SetFocus(hwnd)
        except (AttributeError, OSError, ValueError):
            pass
        root.focus_force()
        entry.focus_force()
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
