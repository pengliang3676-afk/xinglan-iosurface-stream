from pathlib import Path
import unittest


class TouchReleaseSourceTests(unittest.TestCase):
    def test_small_and_master_views_release_at_real_or_last_point(self) -> None:
        source = (Path(__file__).parents[1] / "app.py").read_text(encoding="utf-8")

        release_path = (
            "point = self._normalized(event, clamp=self.owner.touch_manager.enabled)\n"
            "        if point is None:\n"
            "            point = self.last_touch_point"
        )
        self.assertEqual(2, source.count(release_path))
        self.assertEqual(
            2,
            source.count("if point is None:\n            point = self.last_touch_point"),
        )


if __name__ == "__main__":
    unittest.main()
