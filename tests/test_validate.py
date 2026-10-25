import json
import shutil
import tempfile
import unittest
from unittest.mock import patch
from pathlib import Path

from pydantic import ValidationError

from generation import attach_clip, build_prompt, prepare_generation_jobs, review_clip, validate_assets, validate_generation_readiness
from mcp_server import _safe_project_arg
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
        Character.model_validate({"char_id": "lead", "identity": "identity", "looks": {
            "glam": {"description": "glam", "ref_images": []},
            "half": {"description": "half", "ref_images": []},
            "real": {"description": "real", "ref_images": []},
        }})
        Style.model_validate({"base_glam": "glam base", "base_real": "real base", "negative": "negative"})

    def test_speech_timing(self):
        self.assertEqual(speech_need(""), 0.0)
        self.assertAlmostEqual(speech_need("男：你好。女：收到。"), 1.1)

    def test_seed_and_status_contracts(self):
        job = GenJob(job_id="job-a", shot_id=1, attempt=1, expected_clip_path="clips/shot_01_try1.mp4", prompt="p", negative_prompt="n", seed=123, seed_support="best_effort")
        self.assertEqual(job.seed, 123)
        with self.assertRaises(ValidationError):
            GenJob(job_id="job-b", shot_id=1, attempt=1, expected_clip_path="clips/shot_01_try1.mp4", prompt="p", negative_prompt="n", seed_support="always_deterministic")


class PromptTests(unittest.TestCase):
    def test_prompt_order_and_repeatability(self):
        shot = Shot(id=1, start=0, duration=1, shot_type="近景", composition="居中", action="转头", lighting="柔光", camera_move="推进", characters=[{"char_id": "lead", "look": "glam"}])
        chars = {"lead": Character(char_id="lead", identity="固定身份", looks={"glam": Look(description="西装", ref_images=[])})}
        style = Style(base_glam="竖屏写实", base_real="居家写实", negative="不要水印")
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
            (root / "style.json").write_text(json.dumps({"base_glam":"x","base_real":"x","negative":"x"}), encoding="utf-8")
            report = validate_assets(root)
            self.assertFalse(report["ok"])
            self.assertTrue(any("参考图不存在" in e for e in report["errors"]))

    def test_project_assets_validate(self):
        report = validate_assets("projects/chaidan_couple")
        self.assertTrue(report["ok"], report["errors"])
        self.assertEqual(report["checked_refs"], [])

    def test_mcp_project_path_rejects_traversal(self):
        with self.assertRaisesRegex(ValueError, "不允许包含"):
            _safe_project_arg("../../etc")
        with self.assertRaisesRegex(ValueError, "不允许包含"):
            _safe_project_arg("projects/../etc")

    def test_storyboard_transition_shots_use_half_look(self):
        path = Path("projects/chaidan_couple/storyboard.json")
        storyboard = Storyboard.model_validate_json(path.read_text(encoding="utf-8"))
        for shot_id in (4, 5, 7):
            shot = next(shot for shot in storyboard.shots if shot.id == shot_id)
            male = next(item for item in shot.characters if item.char_id == "male_lead")
            self.assertEqual(male.look, "half")

    def test_english_subtitle_limit_is_40_characters(self):
        base = {"title":"x","total_duration":1,"shots":[{"id":1,"start":0,"duration":1,"subtitle":"A"*40}]}
        Storyboard.model_validate(base)
        base["shots"][0]["subtitle"] = "A"*41
        with self.assertRaises(ValidationError):
            Storyboard.model_validate(base)



class JobPreparationTests(unittest.TestCase):
    def _add_real_refs(self, project: Path):
        refs = project / "refs"
        refs.mkdir(exist_ok=True)
        for name, data in (
            ("male_glam.png", b"male-glam-v1"),
            ("female_glam.png", b"female-glam-v1"),
            ("male_real.png", b"male-real-v1"),
            ("female_real.png", b"female-real-v1"),
            ("male_half.png", b"male-half-v1"),
            ("female_half.png", b"female-half-v1"),
        ):
            (refs / name).write_bytes(data)
        chars_path = project / "characters.json"
        chars = json.loads(chars_path.read_text(encoding="utf-8"))
        chars["male_lead"]["looks"]["glam"]["ref_images"] = ["refs/male_glam.png"]
        chars["female_lead"]["looks"]["glam"]["ref_images"] = ["refs/female_glam.png"]
        chars["male_lead"]["looks"]["real"]["ref_images"] = ["refs/male_real.png"]
        chars["female_lead"]["looks"]["real"]["ref_images"] = ["refs/female_real.png"]
        chars["male_lead"]["looks"]["half"]["ref_images"] = ["refs/male_half.png"]
        chars["female_lead"]["looks"]["half"]["ref_images"] = ["refs/female_half.png"]
        chars_path.write_text(json.dumps(chars, ensure_ascii=False, indent=2), encoding="utf-8")

    def test_missing_reference_blocks_jobs_without_prepared_manifests(self):
        source = Path("projects/chaidan_couple")
        with tempfile.TemporaryDirectory() as tmp:
            project = Path(tmp) / "project"
            shutil.copytree(source, project)
            report = validate_generation_readiness(project)
            self.assertFalse(report["ready"])
            self.assertTrue(any("male_lead/glam" in e for e in report["readiness_errors"]))
            blocked = prepare_generation_jobs(project, shot_ids=[1])
            self.assertEqual(blocked["status"], "blocked")
            self.assertEqual(blocked["created"], [])
            self.assertTrue(any("male_lead/glam" in item["reason"] for item in blocked["blocked"]))
            jobs_dir = project / "jobs"
            self.assertFalse(jobs_dir.exists() and list(jobs_dir.glob("*.json")))
            storyboard = json.loads((project / "storyboard.json").read_text(encoding="utf-8"))
            self.assertTrue(all(s.get("gen_status", "pending") == "pending" for s in storyboard["shots"]))

    def test_reference_hash_changes_when_file_content_changes(self):
        source = Path("projects/chaidan_couple")
        with tempfile.TemporaryDirectory() as tmp:
            project = Path(tmp) / "project"
            shutil.copytree(source, project)
            self._add_real_refs(project)
            first = validate_assets(project)
            ref = project / "refs" / "male_glam.png"
            ref.write_bytes(b"male-v2-content-changed")
            second = validate_assets(project)
            first_hash = next(x["sha256"] for x in first["checked_refs"] if x["path"] == "refs/male_glam.png")
            second_hash = next(x["sha256"] for x in second["checked_refs"] if x["path"] == "refs/male_glam.png")
            self.assertNotEqual(first_hash, second_hash)

    def test_jobs_snapshot_stable_prompts_and_unique_attempts(self):
        source = Path("projects/chaidan_couple")
        with tempfile.TemporaryDirectory() as tmp:
            project = Path(tmp) / "project"
            shutil.copytree(source, project)
            self._add_real_refs(project)
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
            self.assertEqual(first_job["attempt"], 1)
            self.assertEqual(first_job["expected_clip_path"], "clips/shot_01_try1.mp4")
            self.assertEqual(len(first_job["ref_images"]), 2)
            self.assertEqual(len(first_job["ref_hashes"]), 2)
            self.assertEqual(second_job["attempt"], 2)
            self.assertEqual(second_job["expected_clip_path"], "clips/shot_01_try2.mp4")



    def test_attach_and_manual_review_state_transition(self):
        source = Path("projects/chaidan_couple")
        with tempfile.TemporaryDirectory() as tmp:
            project = Path(tmp) / "project"
            shutil.copytree(source, project)
            self._add_real_refs(project)
            prepare_generation_jobs(project, shot_ids=[1])
            clips = project / "clips"
            clips.mkdir()
            clip = clips / "shot_01_try1.mp4"
            clip.write_bytes(b"test clip placeholder")
            with patch("generation._probe_duration", return_value=1.8):
                attached = attach_clip(project, 1, "clips/shot_01_try1.mp4")
            self.assertEqual(attached["shot_id"], 1)
            self.assertEqual(attached["actual_duration"], 1.8)
            self.assertTrue(attached["warnings"])
            reviewed = review_clip(project, 1, "accepted", "人工确认动作和人物一致")
            self.assertEqual(reviewed["verdict"], "accepted")
            self.assertEqual(reviewed["shot_status"], "accepted")
            storyboard = json.loads((project / "storyboard.json").read_text(encoding="utf-8"))
            self.assertEqual(storyboard["shots"][0]["gen_status"], "accepted")

if __name__ == "__main__":
    unittest.main()
