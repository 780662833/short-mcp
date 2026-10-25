from __future__ import annotations

import hashlib
import math
import json
import subprocess
import uuid
from pathlib import Path
from typing import Iterable

from pydantic import ValidationError

from schemas import Character, GenJob, Shot, Storyboard, Style

DEFAULT_PROJECT = Path("projects/chaidan_couple")


def _project_path(project: str | Path = DEFAULT_PROJECT) -> Path:
    path = Path(project).resolve()
    if not path.is_dir():
        raise FileNotFoundError(f"项目目录不存在：{path}")
    return path


def _safe_project_file(project_root: Path, relative: str) -> Path:
    raw = Path(relative)
    if raw.is_absolute() or ".." in raw.parts:
        raise ValueError(f"路径必须是项目内相对路径且不能包含 '..'：{relative}")
    candidate = (project_root / raw).resolve()
    if candidate != project_root and project_root not in candidate.parents:
        raise ValueError(f"路径越界，必须位于项目目录内：{relative}")
    return candidate


def _load_json(path: Path):
    return json.loads(path.read_text(encoding="utf-8"))


def _load_project(project_root: Path):
    storyboard = Storyboard.model_validate(_load_json(project_root / "storyboard.json"))
    raw_chars = _load_json(project_root / "characters.json")
    characters: dict[str, Character] = {}
    errors: list[str] = []
    if not isinstance(raw_chars, dict):
        raise ValueError("characters.json 顶层必须是角色对象映射")
    for key, value in raw_chars.items():
        try:
            char = Character.model_validate(value)
            if key != char.char_id:
                errors.append(f"角色键 {key!r} 与 char_id {char.char_id!r} 不一致")
            characters[key] = char
        except ValidationError as exc:
            errors.extend(f"characters.json[{key}]: {e['msg']}" for e in exc.errors())
    style = Style.model_validate(_load_json(project_root / "style.json"))
    return storyboard, characters, style, errors


def validate_assets(project: str | Path = DEFAULT_PROJECT) -> dict:
    """Read-only validation; collect all character/look/reference asset issues."""
    root = _project_path(project)
    errors: list[str] = []
    warnings: list[str] = []
    try:
        storyboard, characters, _style, load_errors = _load_project(root)
        errors.extend(load_errors)
    except (OSError, json.JSONDecodeError, ValidationError, ValueError) as exc:
        return {"ok": False, "structure_ok": False, "ready": False, "errors": [str(exc)], "readiness_errors": [], "warnings": [], "checked_refs": []}

    checked_refs: list[dict] = []
    for shot in storyboard.shots:
        for item in shot.characters:
            char = characters.get(item.char_id)
            if char is None:
                errors.append(f"镜头{shot.id}: 未知 char_id {item.char_id!r}")
                continue
            look = char.looks.get(item.look)
            if look is None:
                errors.append(f"镜头{shot.id}: 角色 {item.char_id!r} 没有 look {item.look!r}")
                continue
            for ref in look.ref_images:
                try:
                    path = _safe_project_file(root, ref)
                    if not path.is_file():
                        # Missing files are readiness failures, not malformed structure.
                        warnings.append(f"镜头{shot.id}: 参考图文件不存在：{ref}")
                        continue
                    digest = hashlib.sha256(path.read_bytes()).hexdigest()
                    checked_refs.append({"path": ref, "sha256": digest})
                except (OSError, ValueError) as exc:
                    errors.append(f"镜头{shot.id}: 参考图路径无效 {ref!r}: {exc}")
    return {"ok": not errors, "structure_ok": not errors, "ready": not errors, "errors": errors, "readiness_errors": [], "warnings": warnings, "checked_refs": checked_refs}



def _image_header_info(data: bytes) -> tuple[str | None, bool]:
    """Identify common raster image signatures without claiming full pixel decoding."""
    if data.startswith(b"\x89PNG\r\n\x1a\n"):
        if len(data) < 24 or data[12:16] != b"IHDR":
            return "png", False
        width = int.from_bytes(data[16:20], "big")
        height = int.from_bytes(data[20:24], "big")
        return "png", width > 0 and height > 0
    if data.startswith(b"\xff\xd8\xff"):
        return "jpeg", len(data) >= 4
    if len(data) >= 12 and data[:4] == b"RIFF" and data[8:12] == b"WEBP":
        return "webp", len(data) >= 16
    if data.startswith((b"GIF87a", b"GIF89a")):
        return "gif", len(data) >= 10
    if data.startswith(b"BM"):
        return "bmp", len(data) >= 26
    if data.startswith((b"II*\x00", b"MM\x00*")):
        return "tiff", len(data) >= 8
    return None, False


def audit_reference_images(project: str | Path = DEFAULT_PROJECT) -> dict:
    """Read-only inventory of configured references, header checks, hashes, and look coverage."""
    root = _project_path(project)
    try:
        storyboard, characters, _style, load_errors = _load_project(root)
    except (OSError, json.JSONDecodeError, ValidationError, ValueError) as exc:
        return {
            "status": "invalid",
            "project": root.name,
            "structure_ok": False,
            "ready": False,
            "errors": [str(exc)],
            "warnings": [],
            "references": [],
            "look_coverage": [],
            "duplicate_content": [],
            "human_review_required": True,
        }
    if load_errors:
        return {
            "status": "invalid",
            "project": root.name,
            "structure_ok": False,
            "ready": False,
            "errors": load_errors,
            "warnings": [],
            "references": [],
            "look_coverage": [],
            "duplicate_content": [],
            "human_review_required": True,
        }

    used_by_look: dict[tuple[str, str], set[int]] = {}
    errors: list[str] = []
    warnings: list[str] = []
    for shot in storyboard.shots:
        for item in shot.characters:
            char = characters.get(item.char_id)
            if char is None:
                errors.append(f"镜头{shot.id}: 未知角色 {item.char_id!r}")
                continue
            if item.look not in char.looks:
                errors.append(f"镜头{shot.id}: 角色 {item.char_id!r} 未配置造型 {item.look!r}")
                continue
            used_by_look.setdefault((item.char_id, item.look), set()).add(shot.id)

    references: list[dict] = []
    path_rows: dict[str, dict] = {}
    hash_paths: dict[str, list[str]] = {}
    look_coverage: list[dict] = []
    supported_extensions = {
        ".png": "png", ".jpg": "jpeg", ".jpeg": "jpeg",
        ".webp": "webp", ".gif": "gif", ".bmp": "bmp", ".tif": "tiff", ".tiff": "tiff",
    }

    for char_id, char in sorted(characters.items()):
        for look_name, look in sorted(char.looks.items()):
            key = (char_id, look_name)
            shot_ids = sorted(used_by_look.get(key, set()))
            configured = list(dict.fromkeys(look.ref_images))
            valid_paths: list[str] = []
            for relative in configured:
                row = path_rows.get(relative)
                if row is None:
                    row = {
                        "path": relative,
                        "exists": False,
                        "valid_image_header": False,
                        "format": None,
                        "sha256": None,
                        "size_bytes": None,
                        "status": "missing",
                        "used_by": [],
                        "errors": [],
                    }
                    try:
                        path = _safe_project_file(root, relative)
                        if path.is_file():
                            data = path.read_bytes()
                            row["exists"] = True
                            row["size_bytes"] = len(data)
                            image_format, header_ok = _image_header_info(data)
                            row["format"] = image_format
                            row["valid_image_header"] = header_ok
                            if not data:
                                row["errors"].append("文件为空")
                            elif image_format is None:
                                row["errors"].append("文件头不是支持的常见图片格式")
                            elif not header_ok:
                                row["errors"].append("图片文件头不完整或尺寸无效")
                            expected_format = supported_extensions.get(path.suffix.lower())
                            if expected_format is None:
                                row["errors"].append("文件扩展名不是支持的图片格式")
                            elif image_format is not None and expected_format != image_format:
                                row["errors"].append(
                                    f"扩展名格式 {expected_format} 与文件头格式 {image_format} 不一致"
                                )
                            if not row["errors"]:
                                row["sha256"] = hashlib.sha256(data).hexdigest()
                                row["status"] = "valid"
                                hash_paths.setdefault(row["sha256"], []).append(relative)
                            else:
                                row["status"] = "invalid"
                        else:
                            row["errors"].append("配置的参考图文件不存在")
                    except (OSError, ValueError) as exc:
                        row["status"] = "invalid"
                        row["errors"].append(str(exc))
                    path_rows[relative] = row
                if key not in row["used_by"]:
                    row["used_by"].append({"char_id": char_id, "look": look_name, "shot_ids": shot_ids})
                if row["status"] == "valid":
                    valid_paths.append(relative)
            look_coverage.append({
                "char_id": char_id,
                "look": look_name,
                "used_by_shots": shot_ids,
                "configured_refs": configured,
                "valid_refs": valid_paths,
                "covered": bool(valid_paths) if shot_ids else bool(valid_paths),
            })

    references = list(path_rows.values())
    for row in references:
        for issue in row["errors"]:
            errors.append(f"参考图 {row['path']}: {issue}")
    # Audit every declared character/look, including currently unused looks, so the
    # inventory can expose incomplete glam/half/real reference sets before later edits.
    missing_looks = [item for item in look_coverage if not item["covered"]]
    for item in missing_looks:
        shots = ", ".join(map(str, item["used_by_shots"])) or "未被当前分镜使用"
        warnings.append(
            f"缺少有效参考图：{item['char_id']}/{item['look']}（镜头 {shots}）"
        )
    duplicates = [
        {"sha256": digest, "paths": sorted(set(paths))}
        for digest, paths in sorted(hash_paths.items())
        if len(set(paths)) > 1
    ]
    ready = not errors and not missing_looks
    status = "ready" if ready else ("invalid" if errors else "blocked")
    return {
        "status": status,
        "project": root.name,
        "structure_ok": True,
        "ready": ready,
        "errors": errors,
        "warnings": warnings,
        "references": sorted(references, key=lambda item: item["path"]),
        "look_coverage": look_coverage,
        "duplicate_content": duplicates,
        "human_review_required": True,
        "image_validation_scope": "header_signature_and_basic_dimensions_only_not_full_decode_or_semantic_review",
        "generation_api_called": False,
    }


def validate_generation_readiness(project: str | Path = DEFAULT_PROJECT) -> dict:
    """Validate structure plus at least one existing reference image for each used character/look."""
    root = _project_path(project)
    structural = validate_assets(root)
    errors = list(structural["errors"])
    if errors:
        return {**structural, "ready": False, "readiness_errors": []}
    storyboard, characters, _style, _load_errors = _load_project(root)
    missing_refs: dict[tuple[str, str], set[int]] = {}
    for shot in storyboard.shots:
        for item in shot.characters:
            char = characters[item.char_id]
            look = char.looks[item.look]
            # Empty lists and lists containing no existing files are both blocked at readiness time.
            existing_refs = []
            for ref in look.ref_images:
                try:
                    ref_path = _safe_project_file(root, ref)
                    if ref_path.is_file():
                        existing_refs.append(ref)
                except ValueError:
                    # Normally caught by structural validation; fail closed if encountered here.
                    pass
            if not existing_refs:
                missing_refs.setdefault((item.char_id, item.look), set()).add(shot.id)
    readiness_errors = [
        f"缺少 {char_id}/{look} 参考图（镜头 {', '.join(map(str, sorted(shot_ids)))})"
        for (char_id, look), shot_ids in sorted(missing_refs.items())
    ]
    return {
        **structural,
        "ready": not readiness_errors,
        "readiness_errors": readiness_errors,
        "errors": errors + readiness_errors,
    }


def project_status(project: str | Path = DEFAULT_PROJECT) -> dict:
    """Read-only project snapshot: structure/readiness, per-shot state, attempts, and blockers."""
    root = _project_path(project)
    validation = validate_assets(root)
    if not validation["structure_ok"]:
        return {
            "project": root.name,
            "status": "invalid",
            "structure_ok": False,
            "ready": False,
            "errors": list(validation["errors"]),
            "warnings": list(validation["warnings"]),
            "missing_references": [],
            "blocked_tasks": [{"scope": "project", "reason": "分镜或项目结构校验失败"}],
            "shots": [],
            "generation_api_called": False,
        }

    storyboard, characters, _style, load_errors = _load_project(root)
    if load_errors:
        return {
            "project": root.name,
            "status": "invalid",
            "structure_ok": False,
            "ready": False,
            "errors": load_errors,
            "warnings": list(validation["warnings"]),
            "missing_references": [],
            "blocked_tasks": [{"scope": "project", "reason": "角色配置校验失败"}],
            "shots": [],
            "generation_api_called": False,
        }

    jobs_by_shot: dict[int, list[dict]] = {}
    job_warnings: list[str] = []
    for job_path in _job_files(root):
        try:
            data = _load_json(job_path)
            shot_id = int(data["shot_id"])
            attempt = int(data["attempt"])
            if attempt < 1:
                raise ValueError("attempt 必须大于等于 1")
            jobs_by_shot.setdefault(shot_id, []).append({
                "job_id": str(data.get("job_id", job_path.stem)),
                "attempt": attempt,
                "status": str(data.get("status", "unknown")),
                "model": str(data.get("model", "")),
                "params": data.get("params", {}),
                "seed": data.get("seed"),
                "seed_support": str(data.get("seed_support", "none")),
                "clip": str(data.get("clip", "")),
                "expected_clip_path": str(data.get("expected_clip_path", "")),
                "manifest": job_path.relative_to(root).as_posix(),
            })
        except (OSError, json.JSONDecodeError, KeyError, TypeError, ValueError) as exc:
            job_warnings.append(f"任务单读取失败 {job_path.relative_to(root).as_posix()}: {exc}")

    missing_by_look: dict[tuple[str, str], set[int]] = {}
    missing_by_shot: dict[int, list[dict]] = {}
    shot_rows: list[dict] = []
    blocked_tasks: list[dict] = []

    for shot in storyboard.shots:
        shot_missing: list[dict] = []
        for item in shot.characters:
            look = characters[item.char_id].looks[item.look]
            existing = []
            for ref in look.ref_images:
                try:
                    ref_path = _safe_project_file(root, ref)
                    if ref_path.is_file():
                        existing.append(ref)
                except (OSError, ValueError):
                    continue
            if not existing:
                key = (item.char_id, item.look)
                missing_by_look.setdefault(key, set()).add(shot.id)
                missing_item = {
                    "char_id": item.char_id,
                    "look": item.look,
                    "configured_refs": list(look.ref_images),
                }
                if missing_item not in shot_missing:
                    shot_missing.append(missing_item)
        if shot_missing:
            missing_by_shot[shot.id] = shot_missing

        attempts = sorted(
            jobs_by_shot.get(shot.id, []),
            key=lambda job: (job["attempt"], job["job_id"]),
        )
        latest = attempts[-1] if attempts else None
        row = {
            "shot_id": shot.id,
            "start": shot.start,
            "duration": shot.duration,
            "gen_status": shot.gen_status,
            "clip": shot.clip,
            "current_attempt": latest["attempt"] if latest else 0,
            "latest_job": latest,
            "attempt_count": len(attempts),
            "missing_references": shot_missing,
        }
        shot_rows.append(row)
        if shot_missing:
            blocked_tasks.append({
                "shot_id": shot.id,
                "reason": "missing_references",
                "details": shot_missing,
            })

    missing_references = [
        {
            "char_id": char_id,
            "look": look,
            "shot_ids": sorted(shot_ids),
        }
        for (char_id, look), shot_ids in sorted(missing_by_look.items())
    ]
    readiness = validate_generation_readiness(root)
    return {
        "project": root.name,
        "status": "ready" if readiness["ready"] else "blocked",
        "structure_ok": True,
        "ready": bool(readiness["ready"]),
        "errors": [],
        "readiness_errors": list(readiness["readiness_errors"]),
        "warnings": list(validation["warnings"]) + job_warnings,
        "missing_references": missing_references,
        "blocked_tasks": blocked_tasks,
        "shots": shot_rows,
        "generation_api_called": False,
    }


def build_prompt(shot: Shot, chars: dict[str, Character], style: Style) -> tuple[str, str, list[str]]:
    # Transition ("half") shots retain the glam visual language; fully-real shots
    # switch to base_real. The rule is deterministic for mixed-character shots.
    looks = [item.look for item in shot.characters]
    base_style = style.base_real if looks and all(look == "real" for look in looks) else style.base_glam
    parts = [base_style, "，".join(p for p in (shot.shot_type, shot.composition) if p)]
    refs: list[str] = []
    for item in shot.characters:
        char = chars[item.char_id]
        look = char.looks[item.look]
        parts.append("，".join(p for p in (char.identity, look.description) if p))
        refs.extend(look.ref_images)
    parts.extend((shot.action, shot.lighting, shot.camera_move))
    prompt = "，".join(p.strip("， ") for p in parts if p and p.strip("， "))
    # Preserve first-use order while deduplicating references.
    refs = list(dict.fromkeys(refs))
    return prompt, style.negative, refs


def prepare_generation_jobs(
    project: str | Path = DEFAULT_PROJECT,
    shot_ids: Iterable[int] | None = None,
    *,
    model: str = "",
    params: dict | None = None,
    seed_support: str = "none",
) -> dict:
    """Build deterministic prompts and unique per-attempt manifests; never calls a model."""
    root = _project_path(project)
    validation = validate_generation_readiness(root)
    if not validation["structure_ok"]:
        return {
            "status": "invalid",
            "created": [],
            "count": 0,
            "errors": validation["errors"],
            "blocked": [],
            "generation_api_called": False,
        }
    if not validation["ready"]:
        return {
            "status": "blocked",
            "created": [],
            "count": 0,
            "errors": [],
            "blocked": [{"reason": issue} for issue in validation["readiness_errors"]],
            "generation_api_called": False,
        }
    storyboard, chars, style, _ = _load_project(root)
    wanted = set(shot_ids) if shot_ids is not None else {s.id for s in storyboard.shots}
    found = {s.id for s in storyboard.shots}
    missing = sorted(wanted - found)
    if missing:
        raise ValueError(f"不存在的镜头编号：{missing}")
    jobs_dir = root / "jobs"
    jobs_dir.mkdir(parents=True, exist_ok=True)
    created: list[dict] = []
    for shot in storyboard.shots:
        if shot.id not in wanted:
            continue
        prompt, negative, refs = build_prompt(shot, chars, style)
        ref_hashes = []
        for ref in refs:
            ref_path = _safe_project_file(root, ref)
            if not ref_path.is_file():
                raise ValueError(f"参考图不存在：{ref}")
            ref_hashes.append(hashlib.sha256(ref_path.read_bytes()).hexdigest())
        existing_attempts = []
        for old_path in _job_files(root):
            try:
                old_data = _load_json(old_path)
                if old_data.get("shot_id") == shot.id:
                    existing_attempts.append(int(old_data.get("attempt", 0)))
            except (OSError, ValueError, TypeError):
                continue
        attempt = max(existing_attempts, default=0) + 1
        job_id = f"shot-{shot.id:02d}-try{attempt}-{uuid.uuid4().hex[:8]}"
        expected_clip_path = f"clips/shot_{shot.id:02d}_try{attempt}.mp4"
        job = GenJob(
            job_id=job_id,
            shot_id=shot.id,
            attempt=attempt,
            expected_clip_path=expected_clip_path,
            prompt=prompt,
            negative_prompt=negative,
            ref_images=refs,
            ref_hashes=ref_hashes,
            seed=shot.seed,
            seed_support=seed_support,
            model=model,
            params=params or {},
        )
        path = jobs_dir / f"{job_id}.json"
        # UUID names make accidental overwrite extremely unlikely; still fail closed.
        if path.exists():
            raise FileExistsError(f"任务单已存在，拒绝覆盖：{path}")
        path.write_text(job.model_dump_json(indent=2) + "\n", encoding="utf-8")
        shot.gen_status = "prepared"
        created.append({"job_id": job_id, "shot_id": shot.id, "attempt": attempt, "expected_clip_path": expected_clip_path, "path": path.relative_to(root).as_posix(), "prompt": prompt})
    (root / "storyboard.json").write_text(
        json.dumps(storyboard.model_dump(mode="json"), ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    return {"status": "prepared", "created": created, "count": len(created), "blocked": [], "generation_api_called": False}


def _probe_duration(path: Path) -> float:
    result = subprocess.run(
        ["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "default=noprint_wrappers=1:nokey=1", str(path)],
        check=True, capture_output=True, text=True,
    )
    duration = float(result.stdout.strip())
    if duration <= 0:
        raise ValueError(f"ffprobe 返回无效时长：{duration}")
    return duration


def _job_files(root: Path) -> list[Path]:
    jobs_dir = root / "jobs"
    return sorted(jobs_dir.glob("shot-*.json"), key=lambda p: (p.stat().st_mtime_ns, p.name)) if jobs_dir.exists() else []


def validate_clip(
    project: str | Path,
    path: str,
    *,
    shot_id: int | None = None,
    require_audio: bool = False,
    min_height: int = 960,
) -> dict:
    """Run read-only ffprobe checks on an existing clip; never renders or modifies files."""
    root = _project_path(project)
    errors: list[str] = []
    warnings: list[str] = []
    try:
        clip_path = _safe_project_file(root, path)
    except ValueError as exc:
        return {"status": "failed", "path": path, "errors": [str(exc)], "warnings": [], "checks": {}, "probe": None}
    if not clip_path.is_file():
        return {"status": "failed", "path": path, "errors": [f"视频素材不存在：{path}"], "warnings": [], "checks": {}, "probe": None}
    if clip_path.stat().st_size == 0:
        return {"status": "failed", "path": path, "errors": [f"视频素材为空文件：{path}"], "warnings": [], "checks": {}, "probe": None}
    if clip_path.suffix.lower() != ".mp4":
        return {"status": "failed", "path": path, "errors": ["素材必须使用 .mp4 扩展名"], "warnings": [], "checks": {}, "probe": None}

    expected_duration = None
    if shot_id is not None:
        try:
            storyboard, _chars, _style, load_errors = _load_project(root)
            if load_errors:
                raise ValueError("; ".join(load_errors))
            shot = next((item for item in storyboard.shots if item.id == shot_id), None)
            if shot is None:
                raise ValueError(f"不存在的镜头编号：{shot_id}")
            expected_duration = shot.duration
        except (OSError, json.JSONDecodeError, ValidationError, ValueError) as exc:
            return {"status": "failed", "path": path, "errors": [str(exc)], "warnings": [], "checks": {}, "probe": None}

    try:
        result = subprocess.run(
            ["ffprobe", "-v", "error", "-show_streams", "-show_format", "-of", "json", str(clip_path)],
            check=True, capture_output=True, text=True,
        )
        probe = json.loads(result.stdout)
        streams = probe.get("streams", [])
        video_streams = [stream for stream in streams if stream.get("codec_type") == "video"]
        audio_streams = [stream for stream in streams if stream.get("codec_type") == "audio"]
        if not video_streams:
            errors.append("未检测到视频流")
            video = {}
        else:
            video = video_streams[0]
        fmt = probe.get("format", {})
        try:
            duration = float(fmt.get("duration") or video.get("duration"))
            if not math.isfinite(duration) or duration <= 0:
                raise ValueError()
        except (TypeError, ValueError):
            duration = None
            errors.append("无法读取有效的视频时长")
        try:
            width, height = int(video["width"]), int(video["height"])
        except (KeyError, TypeError, ValueError):
            width, height = None, None
            if video_streams:
                errors.append("无法读取视频分辨率")
        if width and height:
            if height < min_height:
                errors.append(f"视频高度 {height}px 低于最低要求 {min_height}px")
            if width >= height:
                errors.append(f"视频不是竖屏：{width}x{height}")
            elif abs((width / height) - (9 / 16)) > 0.03:
                warnings.append(f"画面比例 {width}:{height} 偏离 9:16")
        fps = None
        rate = video.get("avg_frame_rate") or video.get("r_frame_rate")
        if rate and rate != "0/0":
            try:
                numerator, denominator = rate.split("/", 1)
                fps = float(numerator) / float(denominator)
                if not math.isfinite(fps) or fps <= 0:
                    fps = None
            except (ValueError, ZeroDivisionError):
                fps = None
        if fps is None and video_streams:
            warnings.append("无法读取有效帧率")
        if not audio_streams:
            message = "未检测到音频流"
            (errors if require_audio else warnings).append(message)
        if expected_duration is not None and duration is not None:
            delta = abs(duration - expected_duration)
            if delta > 0.25:
                errors.append(f"时长偏差 {delta:.3f}s，分镜要求 {expected_duration:.3f}s，实际 {duration:.3f}s")

        checks = {
            "has_video": bool(video_streams),
            "vertical": bool(width and height and height > width),
            "min_resolution": bool(height and height >= min_height),
            "duration_match": None if expected_duration is None or duration is None else abs(duration - expected_duration) <= 0.25,
            "has_audio": bool(audio_streams),
        }
        summary = {
            "duration": duration,
            "width": width,
            "height": height,
            "video_codec": video.get("codec_name"),
            "fps": round(fps, 3) if fps is not None else None,
            "audio_codec": audio_streams[0].get("codec_name") if audio_streams else None,
            "size_bytes": clip_path.stat().st_size,
        }
        return {
            "status": "passed" if not errors else "failed",
            "path": Path(path).as_posix(),
            "shot_id": shot_id,
            "expected_duration": expected_duration,
            "checks": checks,
            "probe": summary,
            "errors": errors,
            "warnings": warnings,
            "human_review_required": True,
        }
    except (OSError, subprocess.SubprocessError, json.JSONDecodeError) as exc:
        return {
            "status": "failed",
            "path": Path(path).as_posix(),
            "shot_id": shot_id,
            "checks": {},
            "probe": None,
            "errors": [f"ffprobe 检查失败：{exc}"],
            "warnings": [],
            "human_review_required": True,
        }


def attach_clip(project: str | Path, shot_id: int, path: str) -> dict:
    root = _project_path(project)
    storyboard, _chars, _style, _errors = _load_project(root)
    shot = next((s for s in storyboard.shots if s.id == shot_id), None)
    if shot is None:
        raise ValueError(f"不存在的镜头编号：{shot_id}")
    clip_path = _safe_project_file(root, path)
    if not clip_path.is_file():
        raise FileNotFoundError(f"视频素材不存在：{path}")
    if clip_path.exists() and clip_path.stat().st_size == 0:
        raise ValueError(f"视频素材为空文件：{path}")
    if not clip_path.name.lower().endswith(".mp4"):
        raise ValueError("输出素材必须使用 .mp4 扩展名，且文件名应与任务单 expected_clip_path 一致")
    actual_duration = _probe_duration(clip_path)
    pending_jobs = []
    for job_path in _job_files(root):
        data = _load_json(job_path)
        if data.get("shot_id") == shot_id and data.get("status") == "prepared":
            pending_jobs.append((job_path, GenJob.model_validate(data)))
    if not pending_jobs:
        raise ValueError(f"镜头{shot_id}没有 prepared 状态的任务单；请先准备新任务")
    job_path, job = max(pending_jobs, key=lambda pair: (pair[1].attempt, pair[0].name))
    expected_path = Path(job.expected_clip_path).as_posix()
    if Path(path).as_posix() != expected_path:
        raise ValueError(f"该任务的输出路径必须是 {expected_path}，拒绝把素材挂到其他尝试或覆盖旧版本")
    if any(Path(root / j.clip).resolve() == clip_path for _p, j in [(p, GenJob.model_validate(_load_json(p))) for p in _job_files(root)] if j.clip):
        raise ValueError(f"该视频路径已被历史任务使用，拒绝覆盖：{path}")
    job.status = "generated"
    job.clip = Path(path).as_posix()
    job.actual_duration = actual_duration
    job_path.write_text(job.model_dump_json(indent=2) + "\n", encoding="utf-8")
    shot.clip = Path(path).as_posix()
    shot.gen_status = "generated"
    (root / "storyboard.json").write_text(
        json.dumps(storyboard.model_dump(mode="json"), ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    warnings = []
    if actual_duration + 0.01 < shot.duration:
        warnings.append(f"素材实际时长 {actual_duration:.3f}s 短于分镜时长 {shot.duration:.3f}s")
    return {"shot_id": shot_id, "job_id": job.job_id, "clip": shot.clip, "actual_duration": actual_duration, "warnings": warnings}


def review_clip(project: str | Path, shot_id: int, verdict: str, note: str = "") -> dict:
    if verdict not in {"accepted", "rejected"}:
        raise ValueError("verdict 只能是 accepted 或 rejected")
    root = _project_path(project)
    storyboard, _chars, _style, _errors = _load_project(root)
    shot = next((s for s in storyboard.shots if s.id == shot_id), None)
    if shot is None:
        raise ValueError(f"不存在的镜头编号：{shot_id}")
    candidates = []
    for job_path in _job_files(root):
        data = _load_json(job_path)
        if data.get("shot_id") == shot_id and data.get("status") == "generated":
            candidates.append((job_path, GenJob.model_validate(data)))
    if not candidates:
        raise ValueError(f"镜头{shot_id}没有待人工验收的 generated 任务")
    job_path, job = max(candidates, key=lambda pair: (pair[1].attempt, pair[0].name))
    job.status = verdict
    job.review_note = note
    job_path.write_text(job.model_dump_json(indent=2) + "\n", encoding="utf-8")
    if verdict == "accepted":
        shot.clip = job.clip
        shot.gen_status = "accepted"
    else:
        # Keep rejected clip only in its job manifest; storyboard.clip must not point to it.
        if shot.clip and Path(shot.clip).as_posix() == Path(job.clip).as_posix():
            shot.clip = ""
        shot.gen_status = "prepared"
    (root / "storyboard.json").write_text(
        json.dumps(storyboard.model_dump(mode="json"), ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    return {"shot_id": shot_id, "job_id": job.job_id, "verdict": verdict, "shot_status": shot.gen_status, "note": note}
