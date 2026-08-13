from __future__ import annotations

import ctypes
from pathlib import Path
from typing import Callable, Sequence


class MusicPlaybackError(RuntimeError):
    """Raised when Windows cannot open or play a configured music track."""


def _send_mci(command: str) -> str:
    """Send one small command to the Windows streaming media service."""
    result = ctypes.create_unicode_buffer(512)
    error_code = ctypes.windll.winmm.mciSendStringW(  # type: ignore[attr-defined]
        command,
        result,
        len(result),
        None,
    )
    if error_code:
        error_text = ctypes.create_unicode_buffer(256)
        ctypes.windll.winmm.mciGetErrorStringW(  # type: ignore[attr-defined]
            error_code,
            error_text,
            len(error_text),
        )
        detail = error_text.value or f"MCI error {error_code}"
        raise MusicPlaybackError(detail)
    return result.value.strip()


class WindowsMusicPlayer:
    """Stream a small playlist through Windows without loading tracks into RAM."""

    ALIAS = "xinglan_background_music"

    def __init__(
        self,
        tracks: Sequence[Path],
        sender: Callable[[str], str] | None = None,
    ) -> None:
        self.tracks = tuple(Path(track) for track in tracks)
        self._sender = sender or _send_mci
        self.current_index = 0
        self._opened = False

    def play(self, index: int) -> None:
        if not self.tracks:
            raise MusicPlaybackError("没有配置音乐文件")
        track_index = index % len(self.tracks)
        track = self.tracks[track_index]
        if not track.is_file():
            raise MusicPlaybackError(f"找不到音乐文件：{track.name}")

        self.close()
        self.current_index = track_index
        escaped_path = str(track).replace('"', '""')
        media_type = "waveaudio" if track.suffix.lower() == ".wav" else "mpegvideo"
        self._sender(
            f'open "{escaped_path}" type {media_type} alias {self.ALIAS}'
        )
        self._opened = True
        try:
            self._sender(f"play {self.ALIAS}")
        except Exception:
            self.close()
            raise

    def mode(self) -> str:
        if not self._opened:
            return "closed"
        return self._sender(f"status {self.ALIAS} mode").lower()

    def close(self) -> None:
        if not self._opened:
            return
        try:
            self._sender(f"stop {self.ALIAS}")
        except MusicPlaybackError:
            pass
        try:
            self._sender(f"close {self.ALIAS}")
        except MusicPlaybackError:
            pass
        self._opened = False
