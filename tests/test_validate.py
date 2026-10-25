import importlib
import json
import math
import os
import unittest
from pathlib import Path

import validate
from pydantic import ValidationError

from validate import Storyboard, speech_need


class SpeechTimingTests(unittest.TestCase):
    def test_empty_dialogue_needs_no_time(self):
        self.assertEqual(speech_need(""), 0.0)

    def test_two_turns_adds_turn_gap(self):
        self.assertAlmostEqual(speech_need("男：行动开始。女：收到。"), 1.5)

    def test_single_turn_has_no_turn_gap(self):
        self.assertAlmostEqual(speech_need("女：剪蓝线！"), 0.6)

    def test_shot_six_and_nine_estimates(self):
        self.assertAlmostEqual(speech_need("男：增高垫！女：发片！"), 1.3)
        self.assertAlmostEqual(speech_need("男&女：重新装！"), 0.6)

    def test_timing_settings_can_be_overridden_by_environment(self):
        names = ("SHORT_MCP_CHARS_PER_SEC", "SHORT_MCP_TURN_GAP")
        previous = {name: os.environ.get(name) for name in names}
        try:
            os.environ[names[0]] = "10"
            os.environ[names[1]] = "0.5"
            importlib.reload(validate)
            self.assertAlmostEqual(
                validate.speech_need("男：你好。女：收到。"), 0.9
            )
        finally:
            for name, value in previous.items():
                if value is None:
                    os.environ.pop(name, None)
                else:
                    os.environ[name] = value
            importlib.reload(validate)

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

    def test_validator_errors_use_real_newlines(self):
        data = {
            "title": "multiple errors",
            "total_duration": 0.2,
            "shots": [
                {
                    "id": 1,
                    "start": 0,
                    "duration": 0.1,
                    "scene": "",
                    "dialogue": "女：abcdefghi",
                },
                {
                    "id": 2,
                    "start": 0.1,
                    "duration": 0.1,
                    "scene": "",
                    "dialogue": "男：abcdefghi",
                },
            ],
        }
        with self.assertRaises(ValidationError) as caught:
            Storyboard.model_validate(data)
        message = caught.exception.errors()[0]["msg"]
        self.assertIn("\n", message)
        self.assertNotIn("\\n", message)

    def test_shot_asset_generation_fields(self):
        base = {
            "title": "asset fields",
            "total_duration": 1,
            "shots": [{
                "id": 1,
                "start": 0,
                "duration": 1,
                "scene": "A character turns toward camera",
                "source": "ai",
                "prompt": "Vertical cinematic shot",
                "ref_image": "assets/characters/lead.png",
                "seed": 1234,
            }],
        }
        storyboard = Storyboard.model_validate(base)
        self.assertEqual(storyboard.shots[0].source, "ai")
        self.assertEqual(storyboard.shots[0].prompt, "Vertical cinematic shot")
        self.assertEqual(storyboard.shots[0].ref_image, "assets/characters/lead.png")
        self.assertEqual(storyboard.shots[0].seed, 1234)

        base["shots"][0]["source"] = "shoot"
        self.assertEqual(Storyboard.model_validate(base).shots[0].source, "shoot")

    def test_shot_source_rejects_unknown_value(self):
        data = {
            "title": "invalid source",
            "total_duration": 1,
            "shots": [{
                "id": 1,
                "start": 0,
                "duration": 1,
                "scene": "A scene",
                "source": "stock",
            }],
        }
        with self.assertRaises(ValidationError):
            Storyboard.model_validate(data)

    def test_updated_storyboard_passes(self):
        path = Path("projects/chaidan_couple/storyboard.json")
        data = json.loads(path.read_text(encoding="utf-8"))
        storyboard = Storyboard.model_validate(data)
        self.assertEqual(len(storyboard.shots), 9)
        self.assertEqual(storyboard.total_duration, 15)


if __name__ == "__main__":
    unittest.main()
