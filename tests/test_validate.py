import json
import shutil
import tempfile
import unittest
from unittest.mock import patch
from pathlib import Path

from pydantic import ValidationError

from generation import audit_reference_images, _safe_project_file, attach_clip, build_prompt, prepare_generation_jobs, project_status, review_clip, validate_assets, validate_clip, validate_generation_readiness
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
        chars = {"lead": Character(char_id="lead", identity="固定身份", looks={
            "glam": Look(description="西装", ref_images=[]),
            "half": Look(description="过渡造型", ref_images=[]),
            "real": Look(description="居家服", ref_images=[]),
        })}
        style = Style(base_glam="竖屏写实", base_real="居家写实", negative="不要水印")
        first = build_prompt(shot, chars, style)
        second = build_prompt(shot, chars, style)
        self.assertEqual(first, second)
        self.assertEqual(first[0], "竖屏写实，近景，居中，固定身份，西装，转头，柔光，推进")
        self.assertEqual(first[1], "不要水印")
        real_shot = Shot(id=1, start=0, duration=1, characters=[{"char_id": "lead", "look": "real"}])
        real_prompt = build_prompt(real_shot, chars, style)
        self.assertTrue(real_prompt[0].startswith("居家写实，"))
        self.assertEqual(real_prompt[1], "不要水印")

    def test_asset_validation_collects_missing_reference(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "storyboard.json").write_text(json.dumps({"title":"x","total_duration":1,"shots":[{"id":1,"start":0,"duration":1,"characters":[{"char_id":"lead","look":"glam"}]}]}), encoding="utf-8")
            (root / "characters.json").write_text(json.dumps({"lead":{"char_id":"lead","identity":"x","looks":{"glam":{"description":"x","ref_images":["refs/missing.png"]}}}}), encoding="utf-8")
            (root / "style.json").write_text(json.dumps({"base_glam":"x","base_real":"x","negative":"x"}), encoding="utf-8")
            report = validate_assets(root)
            self.assertTrue(report["structure_ok"], report["errors"])
            self.assertTrue(any("参考图文件不存在" in warning for warning in report["warnings"]))
            readiness = validate_generation_readiness(root)
            self.assertFalse(readiness["ready"])
            self.assertTrue(any("lead/glam" in issue for issue in readiness["readiness_errors"]))

    def test_reference_audit_reports_missing_looks_and_keeps_generation_disabled(self):
        source = Path("projects/chaidan_couple")
        with tempfile.TemporaryDirectory() as tmp:
            project = Path(tmp) / "project"
            shutil.copytree(source, project)
            report = audit_reference_images(project)
            self.assertEqual(report["status"], "blocked")
            self.assertFalse(report["ready"])
            self.assertFalse(report["generation_api_called"])
            self.assertTrue(report["human_review_required"])
            self.assertTrue(any(row["char_id"] == "male_lead" and row["look"] == "glam" and not row["covered"] for row in report["look_coverage"]))
            self.assertTrue(any("male_lead/glam" in warning for warning in report["warnings"]))

    def test_reference_audit_hashes_valid_png_header_and_detects_invalid_image(self):
        source = Path("projects/chaidan_couple")
        with tempfile.TemporaryDirectory() as tmp:
            project = Path(tmp) / "project"
            shutil.copytree(source, project)
            refs = project / "refs"
            refs.mkdir()
            png_header = b"\x89PNG\r\n\x1a\n" + b"\x00\x00\x00\rIHDR" + (2).to_bytes(4, "big") + (3).to_bytes(4, "big") + b"\x08\x06\x00\x00\x00"
            (refs / "male_glam.png").write_bytes(png_header)
            (refs / "female_glam.png").write_bytes(b"not really an image")
            chars_path = project / "characters.json"
            chars = json.loads(chars_path.read_text(encoding="utf-8"))
            chars["male_lead"]["looks"]["glam"]["ref_images"] = ["refs/male_glam.png"]
            chars["female_lead"]["looks"]["glam"]["ref_images"] = ["refs/female_glam.png"]
            chars_path.write_text(json.dumps(chars, ensure_ascii=False, indent=2), encoding="utf-8")
            report = audit_reference_images(project)
            by_path = {row["path"]: row for row in report["references"]}
            self.assertEqual(by_path["refs/male_glam.png"]["status"], "valid")
            self.assertEqual(len(by_path["refs/male_glam.png"]["sha256"]), 64)
            self.assertEqual(by_path["refs/female_glam.png"]["status"], "invalid")
            self.assertEqual(report["status"], "invalid")
            self.assertFalse(report["ready"])

    def test_project_assets_validate(self):
        report = validate_assets("projects/chaidan_couple")
        self.assertTrue(report["ok"], report["errors"])
        self.assertEqual(report["checked_refs"], [])

    def test_mcp_project_path_rejects_traversal(self):
        with self.assertRaisesRegex(ValueError, "不允许包含"):
            _safe_project_arg("../../etc")
        with self.assertRaisesRegex(ValueError, "不允许包含"):
            _safe_project_arg("projects/../etc")
        with tempfile.TemporaryDirectory() as tmp:
            with self.assertRaisesRegex(ValueError, "不能包含"):
                _safe_project_file(Path(tmp), "clips/../storyboard.json")

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



    def test_project_status_reports_missing_refs_and_blocked_shots_read_only(self):
        source = Path("projects/chaidan_couple")
        with tempfile.TemporaryDirectory() as tmp:
            project = Path(tmp) / "project"
            shutil.copytree(source, project)
            before = (project / "storyboard.json").read_bytes()
            report = project_status(project)
            after = (project / "storyboard.json").read_bytes()
            self.assertEqual(before, after)
            self.assertEqual(report["status"], "blocked")
            self.assertTrue(report["structure_ok"])
            self.assertFalse(report["ready"])
            self.assertFalse(report["generation_api_called"])
            self.assertTrue(report["missing_references"])
            self.assertTrue(report["blocked_tasks"])
            self.assertEqual(len(report["shots"]), 9)
            shot1 = next(row for row in report["shots"] if row["shot_id"] == 1)
            self.assertEqual(shot1["current_attempt"], 0)
            self.assertTrue(shot1["missing_references"])

    def test_project_status_reports_latest_attempt_model_params_and_seed_contract(self):
        source = Path("projects/chaidan_couple")
        with tempfile.TemporaryDirectory() as tmp:
            project = Path(tmp) / "project"
            shutil.copytree(source, project)
            self._add_real_refs(project)
            first = prepare_generation_jobs(
                project, shot_ids=[1], model="test-model",
                params={"width": 720, "height": 1280}, seed_support="best_effort"
            )
            self.assertEqual(first["status"], "prepared")
            report = project_status(project)
            shot1 = next(row for row in report["shots"] if row["shot_id"] == 1)
            self.assertEqual(report["status"], "ready")
            self.assertEqual(shot1["current_attempt"], 1)
            self.assertEqual(shot1["latest_job"]["status"], "prepared")
            self.assertEqual(shot1["latest_job"]["model"], "test-model")
            self.assertEqual(shot1["latest_job"]["params"], {"width": 720, "height": 1280})
            self.assertEqual(shot1["latest_job"]["seed_support"], "best_effort")
            self.assertFalse(report["generation_api_called"])
            storyboard_before = (project / "storyboard.json").read_bytes()
            project_status(project)
            self.assertEqual(storyboard_before, (project / "storyboard.json").read_bytes())

    def test_validate_clip_passes_vertical_video_and_reports_missing_audio_as_warning(self):
        with tempfile.TemporaryDirectory() as tmp:
            project = Path(tmp) / "project"
            shutil.copytree(Path("projects/chaidan_couple"), project)
            clip_path = project / "clips" / "shot_01.mp4"
            clip_path.parent.mkdir(parents=True, exist_ok=True)
            clip_path.write_bytes(b"fake mp4 bytes")
            probe = {
                "streams": [{
                    "codec_type": "video", "codec_name": "h264", "width": 720, "height": 1280,
                    "avg_frame_rate": "30/1", "duration": "2.0"
                }],
                "format": {"duration": "2.0"}
            }
            with patch("generation.subprocess.run", return_value=type("Result", (), {"stdout": json.dumps(probe)})()):
                report = validate_clip(project, "clips/shot_01.mp4", shot_id=1)
            self.assertEqual(report["status"], "passed")
            self.assertTrue(report["checks"]["vertical"])
            self.assertTrue(report["checks"]["duration_match"])
            self.assertFalse(report["checks"]["has_audio"])
            self.assertTrue(any("音频流" in item for item in report["warnings"]))
            self.assertTrue(report["human_review_required"])

    def test_validate_clip_fails_landscape_and_required_audio(self):
        with tempfile.TemporaryDirectory() as tmp:
            project = Path(tmp) / "project"
            shutil.copytree(Path("projects/chaidan_couple"), project)
            clip_path = project / "clips" / "bad.mp4"
            clip_path.parent.mkdir(parents=True, exist_ok=True)
            clip_path.write_bytes(b"fake mp4 bytes")
            probe = {
                "streams": [{
                    "codec_type": "video", "codec_name": "h264", "width": 1920, "height": 1080,
                    "avg_frame_rate": "30/1", "duration": "2.0"
                }],
                "format": {"duration": "2.0"}
            }
            with patch("generation.subprocess.run", return_value=type("Result", (), {"stdout": json.dumps(probe)})()):
                report = validate_clip(project, "clips/bad.mp4", require_audio=True)
            self.assertEqual(report["status"], "failed")
            self.assertTrue(any("不是竖屏" in item for item in report["errors"]))
            self.assertTrue(any("音频流" in item for item in report["errors"]))

    def test_validate_clip_rejects_project_path_traversal(self):
        with tempfile.TemporaryDirectory() as tmp:
            report = validate_clip(Path(tmp), "../outside.mp4")
            self.assertEqual(report["status"], "failed")
            self.assertTrue(report["errors"])

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
