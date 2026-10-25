from __future__ import annotations

import math
import re
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

ROLE = r"(男&女|男|女)[：:]"
CHARS_PER_SEC_ENV = "SHORT_MCP_CHARS_PER_SEC"
TURN_GAP_ENV = "SHORT_MCP_TURN_GAP"
TIMELINE_TOLERANCE = 0.01


def _timing_setting(name: str, default: float, minimum: float) -> float:
    import os

    raw = os.environ.get(name)
    if raw is None:
        return default
    try:
        value = float(raw)
    except ValueError as exc:
        raise ValueError(f"{name}必须是数值，实际为{raw!r}") from exc
    if not math.isfinite(value) or value < minimum:
        raise ValueError(f"{name}必须是有限数值且不小于{minimum:g}")
    return value


def speech_need(dialogue: str) -> float:
    """Estimate minimum spoken time for fast-paced Chinese comedy dialogue."""
    if not dialogue:
        return 0.0
    chars_per_sec = _timing_setting(CHARS_PER_SEC_ENV, 5.0, 0.000001)
    turn_gap = _timing_setting(TURN_GAP_ENV, 0.3, 0.0)
    turns = len(re.findall(ROLE, dialogue))
    text = re.sub(ROLE, "", dialogue)
    chars = len(re.sub(r"[^\u4e00-\u9fffA-Za-z0-9]", "", text))
    return chars / chars_per_sec + turn_gap * max(turns - 1, 0)


class Look(BaseModel):
    model_config = ConfigDict(extra="forbid")
    description: str
    ref_images: list[str] = Field(default_factory=list)


class Character(BaseModel):
    model_config = ConfigDict(extra="forbid")
    char_id: str
    identity: str
    looks: dict[Literal["glam", "half", "real"], Look]


class Style(BaseModel):
    model_config = ConfigDict(extra="forbid")
    base_glam: str
    base_real: str
    negative: str


class CharInShot(BaseModel):
    model_config = ConfigDict(extra="forbid")
    char_id: str
    look: str


class Shot(BaseModel):
    model_config = ConfigDict(extra="forbid")
    id: int = Field(ge=1)
    start: float = Field(ge=0)
    duration: float = Field(gt=0)
    dialogue: str = ""
    subtitle: str = ""
    sfx: str = ""
    clip: str = ""
    characters: list[CharInShot] = Field(default_factory=list)
    shot_type: str = ""
    composition: str = ""
    action: str = ""
    lighting: str = ""
    camera_move: str = ""
    gen_status: Literal["pending", "prepared", "generated", "accepted", "rejected"] = "pending"
    seed: int | None = None

    @model_validator(mode="after")
    def finite_numbers(self):
        if not math.isfinite(self.start):
            raise ValueError("start必须是有限数值")
        if not math.isfinite(self.duration):
            raise ValueError("duration必须是有限数值")
        return self


class Storyboard(BaseModel):
    model_config = ConfigDict(extra="forbid")
    title: str
    total_duration: float = Field(gt=0)
    bgm: str = ""
    shots: list[Shot] = Field(min_length=1)

    @model_validator(mode="after")
    def check_timeline(self):
        errors: list[str] = []
        t = 0.0
        if not math.isfinite(self.total_duration):
            errors.append("total_duration必须是有限数值")
        for expected_id, shot in enumerate(self.shots, start=1):
            if shot.id != expected_id:
                errors.append(f"镜头编号应连续从1开始：第{expected_id}个位置实际是镜头{shot.id}")
            if abs(shot.start - t) > TIMELINE_TOLERANCE + 1e-12:
                errors.append(f"镜头{shot.id}: start应为{t:g}，实际{shot.start:g}")
            t += shot.duration
            if len(shot.subtitle) > 40:
                errors.append(f"镜头{shot.id}: 字幕{len(shot.subtitle)}个字符，超过40字符")
            need = speech_need(shot.dialogue)
            if need > shot.duration + TIMELINE_TOLERANCE:
                errors.append(f"镜头{shot.id}: 台词预计{need:.1f}s，镜头{shot.duration:g}s，超出{need - shot.duration:.1f}s")
        if math.isfinite(self.total_duration) and abs(t - self.total_duration) > TIMELINE_TOLERANCE + 1e-12:
            errors.append(f"时长之和{t:g} ≠ total_duration {self.total_duration:g}")
        if errors:
            raise ValueError("\n".join(errors))
        return self


class GenJob(BaseModel):
    model_config = ConfigDict(extra="forbid")
    job_id: str
    shot_id: int
    attempt: int = Field(ge=1)
    expected_clip_path: str
    prompt: str
    negative_prompt: str
    ref_images: list[str] = Field(default_factory=list)
    ref_hashes: list[str] = Field(default_factory=list)
    seed: int | None = None
    seed_support: Literal["none", "best_effort", "deterministic"] = "none"
    model: str = ""
    params: dict = Field(default_factory=dict)
    status: Literal["prepared", "generated", "accepted", "rejected"] = "prepared"
    clip: str = ""
    actual_duration: float | None = None
    review_note: str = ""
