from __future__ import annotations

import json
import logging
import queue
import subprocess
import sys
import threading
from pathlib import Path
from typing import Callable


Message = list[object]
LOGGER = logging.getLogger(__name__)

# Windows normally shows the "app starting" busy ring beside the pointer when
# the foreground process launches a helper.  The IME helper is intentionally
# restarted after every phone click so its native IME memory can be reclaimed;
# suppress only that cursor feedback and keep the existing lifetime policy.
_STARTF_FORCEOFFFEEDBACK = 0x00000080


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

    @classmethod
    def _worker_command(
        cls,
        screen_x: int,
        screen_y: int,
        owner_hwnd: int = 0,
        anchor_x: int = 0,
        anchor_y: int = 0,
    ) -> list[str]:
        coordinates = [
            "--x",
            str(int(screen_x)),
            "--y",
            str(int(screen_y)),
            "--owner-hwnd",
            str(int(owner_hwnd)),
            "--anchor-x",
            str(int(anchor_x)),
            "--anchor-y",
            str(int(anchor_y)),
        ]
        if getattr(sys, "frozen", False):
            helper = Path(sys.executable).with_name("星澜输入.exe")
            if helper.is_file():
                return [str(helper), *coordinates]
            return [sys.executable, "--ime-worker", *coordinates]
        app_path = Path(__file__).resolve().parents[1] / "app.py"
        return [cls._worker_executable(), "-u", str(app_path), "--ime-worker", *coordinates]

    @staticmethod
    def _startup_info() -> subprocess.STARTUPINFO | None:
        if not hasattr(subprocess, "STARTUPINFO"):
            return None
        startup_info = subprocess.STARTUPINFO()
        startup_info.dwFlags |= _STARTF_FORCEOFFFEEDBACK
        return startup_info

    def activate(self, screen_x: int, screen_y: int) -> None:
        self.deactivate()
        if self._closing:
            return
        self._generation += 1
        generation = self._generation
        owner_hwnd = 0
        anchor_x = 0
        anchor_y = 0
        try:
            # Keep a client-relative anchor as well as the initial absolute
            # fallback.  The helper can then follow the main window without
            # restarting IME composition while the user moves the app.
            self.root.update_idletasks()
            owner_hwnd = int(self.root.winfo_id())
            anchor_x = int(screen_x) - int(self.root.winfo_rootx())
            anchor_y = int(screen_y) - int(self.root.winfo_rooty())
        except (AttributeError, TypeError, ValueError):
            pass
        command = self._worker_command(
            screen_x,
            screen_y,
            owner_hwnd,
            anchor_x,
            anchor_y,
        )
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
            startupinfo=self._startup_info(),
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
            elif kind == "candidate_moved" and len(message) >= 4:
                LOGGER.info(
                    "IME candidate moved hwnd=%s target=%s,%s",
                    message[1],
                    message[2],
                    message[3],
                )
            elif kind == "caret_position" and len(message) >= 3:
                LOGGER.info(
                    "IME native caret target=%s,%s",
                    message[1],
                    message[2],
                )
            elif kind == "tsf_style" and len(message) >= 3:
                LOGGER.info(
                    "IME Rich Edit TSF enabled=%s style=0x%x",
                    message[1],
                    int(message[2]),
                )
            elif kind == "ime_geometry" and len(message) >= 2:
                LOGGER.info("IME native geometry=%s", message[1])
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
        # This method is called from Tk's mouse-release handler.  Waiting for a
        # frozen helper process here blocks the whole UI for up to 1.6 seconds,
        # which is especially visible in the packaged EXE.  Ask the old worker
        # to stop immediately, then reap/kill it on a background thread.
        if process.poll() is None:
            try:
                process.terminate()
            except OSError:
                pass
        threading.Thread(
            target=self._reap_process,
            args=(process,),
            name="xinglan-ime-reaper",
            daemon=True,
        ).start()

    @staticmethod
    def _reap_process(process: subprocess.Popen[str]) -> None:
        try:
            process.wait(timeout=0.6)
        except subprocess.TimeoutExpired:
            try:
                process.kill()
            except OSError:
                pass
            try:
                process.wait(timeout=1.0)
            except (OSError, subprocess.TimeoutExpired):
                pass
        except OSError:
            pass
        if process.stdout is not None:
            try:
                process.stdout.close()
            except OSError:
                pass

    def close(self) -> None:
        self._closing = True
        self.deactivate()
