from __future__ import annotations

import json
import queue
import subprocess
import sys
import threading
from pathlib import Path
from typing import Callable


Message = list[object]


class ImeWorkerClient:
    """Control a lightweight IME subprocess without importing the app there."""

    def __init__(
        self,
        root: object,
        on_text: Callable[[str], None],
        on_key: Callable[[int, int, str], None],
    ) -> None:
        self.root = root
        self.on_text = on_text
        self.on_key = on_key
        self._messages: queue.SimpleQueue[tuple[int, Message]] = queue.SimpleQueue()
        self._process: subprocess.Popen[str] | None = None
        self._generation = 0
        self._closing = False
        self.root.after(20, self._poll)

    @staticmethod
    def _worker_executable() -> str:
        executable = Path(sys.executable)
        pythonw = executable.with_name("pythonw.exe")
        return str(pythonw if pythonw.exists() else executable)

    def activate(self, screen_x: int, screen_y: int) -> None:
        self.deactivate()
        if self._closing:
            return
        self._generation += 1
        generation = self._generation
        command = [
            self._worker_executable(),
            "-u",
            "-m",
            "xinglan.ime_worker",
            "--x",
            str(max(1, int(screen_x))),
            "--y",
            str(max(1, int(screen_y))),
        ]
        creation_flags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
        process = subprocess.Popen(
            command,
            cwd=str(Path(__file__).resolve().parents[1]),
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            text=True,
            encoding="utf-8",
            errors="replace",
            bufsize=1,
            creationflags=creation_flags,
        )
        self._process = process
        threading.Thread(
            target=self._read_messages,
            args=(generation, process),
            name="xinglan-ime-reader",
            daemon=True,
        ).start()

    def _read_messages(
        self,
        generation: int,
        process: subprocess.Popen[str],
    ) -> None:
        stream = process.stdout
        if stream is None:
            return
        try:
            for line in stream:
                try:
                    message = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if isinstance(message, list) and message:
                    self._messages.put((generation, message))
        finally:
            try:
                stream.close()
            except OSError:
                pass

    def _poll(self) -> None:
        if self._closing:
            return
        while True:
            try:
                generation, message = self._messages.get_nowait()
            except queue.Empty:
                break
            if generation != self._generation:
                continue
            kind = message[0]
            if kind == "text" and len(message) >= 2:
                self.on_text(str(message[1]))
            elif kind == "key" and len(message) >= 4:
                self.on_key(int(message[1]), int(message[2]), str(message[3]))
        process = self._process
        if process is not None and process.poll() is not None:
            try:
                process.wait(timeout=0)
            except (OSError, subprocess.TimeoutExpired):
                pass
            self._process = None
        self.root.after(20, self._poll)

    def deactivate(self) -> None:
        self._generation += 1
        process = self._process
        self._process = None
        if process is None:
            return
        if process.poll() is None:
            process.terminate()
            try:
                process.wait(timeout=0.6)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait(timeout=1.0)
        if process.stdout is not None:
            try:
                process.stdout.close()
            except OSError:
                pass

    def close(self) -> None:
        self._closing = True
        self.deactivate()
