from __future__ import annotations

import multiprocessing
import tkinter as tk
from multiprocessing.connection import Connection
from typing import Callable


Message = tuple[object, ...]


def _ime_process_main(connection: Connection) -> None:
    """Own the Windows IME in a disposable process.

    Microsoft IME native composition memory is then owned by this helper, not
    by the long-running projection process.  After an input burst the helper
    exits; Windows releases the whole native IME heap in one operation.
    """
    root = tk.Tk()
    root.withdraw()
    root.overrideredirect(True)
    root.attributes("-topmost", True)
    root.configure(bg="#1d2939", cursor="arrow")
    buffer = tk.StringVar(value="")
    busy = False
    consume_job: str | None = None
    recycle_job: str | None = None
    dirty = False

    entry = tk.Entry(
        root,
        textvariable=buffer,
        borderwidth=0,
        highlightthickness=0,
        takefocus=True,
        cursor="arrow",
    )
    entry.place(x=0, y=0, width=1, height=1)

    def safe_send(message: Message) -> bool:
        try:
            connection.send(message)
            return True
        except (BrokenPipeError, EOFError, OSError):
            root.destroy()
            return False

    def recycle() -> None:
        if dirty:
            safe_send(("recycle",))
            root.destroy()

    def note_activity(_event: tk.Event | None = None) -> None:
        nonlocal recycle_job, dirty
        dirty = True
        if recycle_job is not None:
            root.after_cancel(recycle_job)
        # Long enough not to interrupt normal candidate selection.  Once the
        # user pauses, terminating this process returns all native IME memory.
        recycle_job = root.after(5000, recycle)

    def consume() -> None:
        nonlocal busy, consume_job
        consume_job = None
        text = buffer.get()
        if not text:
            return
        busy = True
        try:
            buffer.set("")
        finally:
            busy = False
        note_activity()
        safe_send(("text", text))

    def changed(*_args: object) -> None:
        nonlocal consume_job
        if busy:
            return
        if consume_job is None:
            consume_job = root.after_idle(consume)

    def paste(_event: tk.Event | None = None) -> str:
        try:
            text = root.clipboard_get()
        except tk.TclError:
            text = ""
        note_activity()
        if text:
            safe_send(("text", text))
        return "break"

    def send_key(page: int, usage: int, label: str) -> str:
        note_activity()
        safe_send(("key", page, usage, label))
        return "break"

    def poll_parent() -> None:
        try:
            while connection.poll():
                message = connection.recv()
                if not message:
                    continue
                if message[0] == "activate":
                    x, y = int(message[1]), int(message[2])
                    root.geometry(f"1x1+{x}+{y}")
                    root.deiconify()
                    root.lift()
                    root.after_idle(entry.focus_force)
                elif message[0] == "shutdown":
                    root.destroy()
                    return
        except (EOFError, OSError, tk.TclError):
            try:
                root.destroy()
            except tk.TclError:
                pass
            return
        root.after(15, poll_parent)

    buffer.trace_add("write", changed)
    entry.bind("<KeyPress>", note_activity, add="+")
    entry.bind("<Control-v>", paste)
    entry.bind("<Control-V>", paste)
    entry.bind("<Return>", lambda _event: send_key(0x07, 0x28, "回车"))
    entry.bind("<KP_Enter>", lambda _event: send_key(0x07, 0x28, "回车"))
    entry.bind("<BackSpace>", lambda _event: send_key(0x07, 0x2A, "退格"))
    root.after(15, poll_parent)
    try:
        root.mainloop()
    finally:
        try:
            connection.close()
        except OSError:
            pass


class ImeBridgeClient:
    """Tk-side controller for the disposable IME helper process."""

    def __init__(
        self,
        root: tk.Misc,
        on_text: Callable[[str], None],
        on_key: Callable[[int, int, str], None],
    ) -> None:
        self.root = root
        self.on_text = on_text
        self.on_key = on_key
        self._context = multiprocessing.get_context("spawn")
        self._connection: Connection | None = None
        self._process: multiprocessing.Process | None = None
        self._active = False
        self._anchor = (1, 1)
        self._closing = False
        self.root.after(20, self._poll)

    def _start(self) -> None:
        parent, child = self._context.Pipe(duplex=True)
        process = self._context.Process(
            target=_ime_process_main,
            args=(child,),
            name="xinglan-ime",
            daemon=True,
        )
        process.start()
        child.close()
        self._connection = parent
        self._process = process

    def _stop_process(self) -> None:
        connection, process = self._connection, self._process
        self._connection = None
        self._process = None
        if connection is not None:
            try:
                connection.send(("shutdown",))
            except (BrokenPipeError, EOFError, OSError):
                pass
            try:
                connection.close()
            except OSError:
                pass
        if process is not None:
            if process.is_alive():
                process.join(0.15)
                if process.is_alive():
                    process.terminate()
                    process.join(0.5)
            else:
                process.join(0)
            try:
                process.close()
            except (OSError, ValueError):
                pass

    def activate(self, screen_x: int, screen_y: int) -> None:
        self._active = True
        self._anchor = (max(1, screen_x), max(1, screen_y))
        if self._process is None or not self._process.is_alive():
            self._stop_process()
            self._start()
        if self._connection is not None:
            try:
                self._connection.send(("activate", *self._anchor))
            except (BrokenPipeError, EOFError, OSError):
                self._stop_process()

    def _poll(self) -> None:
        if self._closing:
            return
        connection = self._connection
        if connection is not None:
            try:
                while connection.poll():
                    message = connection.recv()
                    if not message:
                        continue
                    if message[0] == "text":
                        self.on_text(str(message[1]))
                    elif message[0] == "key":
                        self.on_key(int(message[1]), int(message[2]), str(message[3]))
            except (BrokenPipeError, EOFError, OSError):
                self._stop_process()
        if (
            self._active
            and (self._process is None or not self._process.is_alive())
        ):
            self._stop_process()
            self._start()
            if self._connection is not None:
                self._connection.send(("activate", *self._anchor))
        self.root.after(20, self._poll)

    def close(self) -> None:
        self._closing = True
        self._active = False
        self._stop_process()
