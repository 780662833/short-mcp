from pathlib import Path
from typing import Annotated

from mcp.server.fastmcp import FastMCP

from generation import attach_clip as attach_clip_impl
from generation import prepare_generation_jobs as prepare_jobs_impl
from generation import review_clip as review_clip_impl
from generation import validate_assets as validate_assets_impl

mcp = FastMCP("short-mcp")


@mcp.tool()
def validate_assets(project: str = "projects/chaidan_couple") -> dict:
    """Read-only validation of storyboard, character/look references, and asset paths."""
    return validate_assets_impl(project)


@mcp.tool()
def prepare_generation_jobs(
    project: str = "projects/chaidan_couple",
    shot_ids: list[int] | None = None,
    model: str = "",
    seed_support: str = "none",
) -> dict:
    """Build deterministic prompt snapshots and job manifests. Does not call generation APIs."""
    return prepare_jobs_impl(project, shot_ids, model=model, seed_support=seed_support)


@mcp.tool()
def attach_clip(project: str, shot_id: int, path: str) -> dict:
    """Attach an existing local video file to the latest prepared job for a shot."""
    return attach_clip_impl(project, shot_id, path)


@mcp.tool()
def review_clip(project: str, shot_id: int, verdict: str, note: str = "") -> dict:
    """Record an explicit human verdict: accepted or rejected."""
    return review_clip_impl(project, shot_id, verdict, note)


if __name__ == "__main__":
    mcp.run()
