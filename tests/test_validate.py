import json
import math
import unittest
from pathlib import Path

from pydantic import ValidationError

from validate import Storyboard, speech_need


class SpeechTimingTests(unittest.TestCase):
    def test_empty_dialogue_needs_no_time(self):
        self.assertEqual(speech_need(""), 0.0)

    def test_two_turns_adds_turn_gap(self):
        self.assertAlmostEqual(speech_need("男：行动开始。女：收到。"), 1.5)

    def test_single_turn_has_no_turn_gap(self):
        self.assertAlmostEqual(speech_need("女：剪蓝线！"), 0.6)

    def test_rejects_non_finite_timeline_values(self):
        data = {
            "title": "invalid",
            "total_duration": math.inf,
            "shots": [{"id": 1, "start": 0, "duration": math.inf, "scene": ""}],
        }
        with self.assertRaises(ValidationError):
            Storyboard.model_validate(data)

    def test_timeline_tolerance_accepts_float_rounding_boundary(self):
        data = {
            "title": "tolerance",
            "total_duration": 0.31,
            "shots": [
                {"id": 1, "start": 0, "duration": 0.3, "scene": ""},
                {"id": 2, "start": 0.31, "duration": 0.01, "scene": ""},
            ],
        }
        Storyboard.model_validate(data)

    def test_original_storyboard_reports_all_overruns(self):
        path = Path("projects/chaidan_couple/storyboard.json")
        data = json.loads(path.read_text(encoding="utf-8"))
        with self.assertRaises(ValidationError) as caught:
            Storyboard.model_validate(data)

        message = str(caught.exception)
        for shot_id in (3, 4, 5, 6, 8):
            self.assertIn(f"镜头{shot_id}", message)


if __name__ == "__main__":
    unittest.main()
