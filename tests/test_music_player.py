from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from xinglan.music_player import MusicPlaybackError, WindowsMusicPlayer


class WindowsMusicPlayerTests(unittest.TestCase):
    def test_play_streams_selected_track_and_close_releases_alias(self) -> None:
        commands: list[str] = []

        def sender(command: str) -> str:
            commands.append(command)
            return "playing" if command.startswith("status ") else ""

        with tempfile.TemporaryDirectory() as temp_dir:
            first = Path(temp_dir) / "first.mp3"
            second = Path(temp_dir) / "second.wav"
            first.touch()
            second.touch()
            player = WindowsMusicPlayer((first, second), sender=sender)

            player.play(1)
            self.assertEqual(1, player.current_index)
            self.assertIn(str(second), commands[0])
            self.assertIn("type waveaudio", commands[0])
            self.assertEqual("playing", player.mode())

            player.close()

        self.assertIn("stop xinglan_background_music", commands)
        self.assertIn("close xinglan_background_music", commands)

    def test_missing_track_is_reported_before_open(self) -> None:
        player = WindowsMusicPlayer((Path("missing.mp3"),), sender=lambda _: "")
        with self.assertRaises(MusicPlaybackError):
            player.play(0)


if __name__ == "__main__":
    unittest.main()
