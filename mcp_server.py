from pathlib import Path
from typing import Annotated

from mcp.server.fastmcp import FastMCP

from generation import attach_clip as attach_clip_impl
from generation import audit_reference_images as audit_reference_images_impl
from generation import prepare_generation_jobs as prepare_jobs_impl
from generation import project_status as project_status_impl
from generation import review_clip as review_clip_impl
from generation import validate_assets as validate_assets_impl
from generation import validate_clip as validate_clip_impl
from generation import validate_generation_readiness as validate_readiness_impl

mcp = FastMCP("short-mcp")
_REPO_ROOT = Path(__file__).resolve().parent
_PROJECTS_ROOT = (_REPO_ROOT / "projects").resolve()


def _safe_project_arg(project: str) -> str:
    """Resolve MCP-supplied project paths strictly inside the repository projects/ directory."""
    raw = Path(project)
    if ".." in raw.parts:
        raise ValueError(f"项目路径不允许包含 '..'：{project}")
    if raw.is_absolute():
        candidate = raw.resolve()
    elif raw.parts and raw.parts[0] == "projects":
        candidate = (_REPO_ROOT / raw).resolve()
    else:
        candidate = (_PROJECTS_ROOT / raw).resolve()
    if candidate == _PROJECTS_ROOT or _PROJECTS_ROOT not in candidate.parents:
        raise ValueError(f"项目路径越界，必须位于 projects/ 子目录内：{project}")
    if not candidate.is_dir():
        raise FileNotFoundError(f"项目目录不存在：{candidate}")
    return str(candidate)


@mcp.tool()
def validate_assets(project: str = "projects/chaidan_couple") -> dict:
    """Read-only validation of storyboard, character/look references, and asset paths."""
    return validate_assets_impl(_safe_project_arg(project))


@mcp.tool()
def audit_reference_images(project: str = "projects/chaidan_couple") -> dict:
    """Read-only reference image inventory, file-header validation, SHA-256, and character/look coverage."""
    return audit_reference_images_impl(_safe_project_arg(project))


@mcp.tool()
def validate_generation_readiness(project: str = "projects/chaidan_couple") -> dict:
    """Validate that every character/look used by the storyboard has at least one existing reference image."""
    return validate_readiness_impl(_safe_project_arg(project))


@mcp.tool()
def project_status(project: str = "projects/chaidan_couple") -> dict:
    """Read-only snapshot of project readiness, per-shot generation status, attempts, and blockers."""
    return project_status_impl(_safe_project_arg(project))


@mcp.tool()
def prepare_generation_jobs(
    project: str = "projects/chaidan_couple",
    shot_ids: list[int] | None = None,
    model: str = "",
    seed_support: str = "none",
    params: dict | None = None,
) -> dict:
    """Build deterministic prompt snapshots and job manifests. Does not call generation APIs."""
    return prepare_jobs_impl(_safe_project_arg(project), shot_ids, model=model, params=params, seed_support=seed_support)


@mcp.tool()
def validate_clip(project: str = "projects/chaidan_couple", path: str = "", shot_id: int | None = None, require_audio: bool = False, min_height: int = 960) -> dict:
    """Read-only ffprobe checks for clip integrity, vertical format, resolution, duration, and audio presence."""
    return validate_clip_impl(_safe_project_arg(project), path, shot_id=shot_id, require_audio=require_audio, min_height=min_height)


@mcp.tool()
def attach_clip(project: str, shot_id: int, path: str) -> dict:
    """Attach an existing local video file to the latest prepared job for a shot."""
    return attach_clip_impl(_safe_project_arg(project), shot_id, path)


@mcp.tool()
def review_clip(project: str, shot_id: int, verdict: str, note: str = "") -> dict:
    """Record an explicit human verdict: accepted or rejected."""
    return review_clip_impl(_safe_project_arg(project), shot_id, verdict, note)


if __name__ == "__main__":
    mcp.run()
