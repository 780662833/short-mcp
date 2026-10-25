import json
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
