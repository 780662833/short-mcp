import json
import tempfile
import unittest
from pathlib import Path

from pydantic import ValidationError

from generation import build_prompt, prepare_generation_jobs, validate_assets
from schemas import Character, GenJob, Look, Shot, Storyboard, Style, speech_need


class SchemaTests(unittest.TestCase):
    def test_storyboard_has_nine_contiguous_shots(self):
        path = Path("projects/chaidan_couple/storyboard.json")
        storyboard = Storyboard.model_validate_json(path.read_text(encoding="utf-8"))
        self.assertEqual(len(storyboard.shots), 9)
        self.assertEqual(storyboard.total_duration, 15)
        self.assertTrue(all(not shot.dialogue for shot in storyboard.shots))

    def test_rejects_unknown_fields(self):
        with self.assertRaises(ValidationError):
            Look.model_validate({"description": "look", "ref_images": [], "unexpected": True})

    def test_character_and_style_models(self):
        Character.model_validate({"char_id": "lead", "identity": "identity", "looks": {"glam": {"description": "look", "ref_images": []}}})
        Style.model_validate({"base": "base", "negative": "negative"})

    def test_speech_timing(self):
        self.assertEqual(speech_need(""), 0.0)
        self.assertAlmostEqual(speech_need("男：你好。女：收到。"), 1.1)

    def test_seed_and_status_contracts(self):
        job = GenJob(job_id="job-a", shot_id=1, prompt="p", negative_prompt="n", seed=123, seed_support="best_effort")
        self.assertEqual(job.seed, 123)
        with self.assertRaises(ValidationError):
            GenJob(job_id="job-b", shot_id=1, prompt="p", negative_prompt="n", seed_support="always_deterministic")


class PromptTests(unittest.TestCase):
    def test_prompt_order_and_repeatability(self):
        shot = Shot(id=1, start=0, duration=1, shot_type="近景", composition="居中", action="转头", lighting="柔光", camera_move="推进", characters=[{"char_id": "lead", "look": "glam"}])
        chars = {"lead": Character(char_id="lead", identity="固定身份", looks={"glam": Look(description="西装", ref_images=[])})}
        style = Style(base="竖屏写实", negative="不要水印")
        first = build_prompt(shot, chars, style)
        second = build_prompt(shot, chars, style)
        self.assertEqual(first, second)
        self.assertEqual(first[0], "竖屏写实，近景，居中，固定身份，西装，转头，柔光，推进")
        self.assertEqual(first[1], "不要水印")

    def test_asset_validation_collects_missing_reference(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "storyboard.json").write_text(json.dumps({"title":"x","total_duration":1,"shots":[{"id":1,"start":0,"duration":1,"characters":[{"char_id":"lead","look":"glam"}]}]}), encoding="utf-8")
            (root / "characters.json").write_text(json.dumps({"lead":{"char_id":"lead","identity":"x","looks":{"glam":{"description":"x","ref_images":["refs/missing.png"]}}}}), encoding="utf-8")
            (root / "style.json").write_text(json.dumps({"base":"x","negative":"x"}), encoding="utf-8")
            report = validate_assets(root)
            self.assertFalse(report["ok"])
            self.assertTrue(any("参考图不存在" in e for e in report["errors"]))

    def test_project_assets_validate(self):
        report = validate_assets("projects/chaidan_couple")
        self.assertTrue(report["ok"], report["errors"])
        self.assertEqual(report["checked_refs"], [])



class JobPreparationTests(unittest.TestCase):
    def test_jobs_snapshot_stable_prompts_and_unique_attempts(self):
        source = Path("projects/chaidan_couple")
        with tempfile.TemporaryDirectory() as tmp:
            project = Path(tmp) / "project"
            shutil.copytree(source, project)
            first = prepare_generation_jobs(project, shot_ids=[1])
            first_job_path = project / first["created"][0]["path"]
            first_job = json.loads(first_job_path.read_text(encoding="utf-8"))
            second = prepare_generation_jobs(project, shot_ids=[1])
            second_job_path = project / second["created"][0]["path"]
            second_job = json.loads(second_job_path.read_text(encoding="utf-8"))
            self.assertFalse(first["generation_api_called"])
            self.assertNotEqual(first_job["job_id"], second_job["job_id"])
            self.assertEqual(first_job["prompt"], second_job["prompt"])
            self.assertEqual(first_job["negative_prompt"], second_job["negative_prompt"])
            self.assertEqual(first_job["seed"], 20261)
            self.assertEqual(first_job["ref_images"], [])
            self.assertEqual(first_job["ref_hashes"], [])


if __name__ == "__main__":
    unittest.main()
