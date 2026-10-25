import json
import math
import re
import sys
from pathlib import Path
from typing import List

from pydantic import BaseModel, ConfigDict, Field, ValidationError, model_validator


ROLE = r"(男&女|男|女)[：:]"
CHARS_PER_SEC = 5.0
TURN_GAP = 0.3
TIMELINE_TOLERANCE = 0.01


def speech_need(dialogue: str) -> float:
    """Estimate minimum spoken time for fast-paced Chinese comedy dialogue."""
    if not dialogue:
        return 0.0
    turns = len(re.findall(ROLE, dialogue))
    text = re.sub(ROLE, "", dialogue)
    chars = len(re.sub(r"[^\u4e00-\u9fffA-Za-z0-9]", "", text))
    return chars / CHARS_PER_SEC + TURN_GAP * max(turns - 1, 0)


class Shot(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: int = Field(ge=1)
    start: float = Field(ge=0)
    duration: float = Field(gt=0)
    scene: str
    dialogue: str = ""
    subtitle: str = ""
    sfx: str = ""
    clip: str = ""


class Storyboard(BaseModel):
    model_config = ConfigDict(extra="forbid")

    title: str
    total_duration: float = Field(gt=0)
    bgm: str = ""
    shots: List[Shot] = Field(min_length=1)

    @model_validator(mode="after")
    def check_timeline(self):
        if not math.isfinite(self.total_duration):
            raise ValueError("total_duration必须是有限数值")

        t = 0.0
        for expected_id, shot in enumerate(self.shots, start=1):
            if not math.isfinite(shot.start):
                raise ValueError(f"镜头{shot.id}: start必须是有限数值")
            if not math.isfinite(shot.duration):
                raise ValueError(f"镜头{shot.id}: duration必须是有限数值")
            if shot.id != expected_id:
                raise ValueError(
                    f"镜头编号应连续从1开始：第{expected_id}个位置实际是镜头{shot.id}"
                )
            if abs(shot.start - t) > TIMELINE_TOLERANCE + 1e-12:
                raise ValueError(
                    f"镜头{shot.id}: start应为{t:.3f}，实际{shot.start}"
                )
            t += shot.duration

        if abs(t - self.total_duration) > TIMELINE_TOLERANCE + 1e-12:
            raise ValueError(
                f"时长之和{t:.3f} ≠ total_duration {self.total_duration}"
            )

        for shot in self.shots:
            if len(shot.subtitle) > 12:
                raise ValueError(
                    f"镜头{shot.id}: 字幕超过12字（当前{len(shot.subtitle)}字）"
                )

        speech_errors = []
        for shot in self.shots:
            need = speech_need(shot.dialogue)
            if need > shot.duration + TIMELINE_TOLERANCE:
                speech_errors.append(
                    f"镜头{shot.id}: 台词约需{need:.1f}秒，"
                    f"镜头仅{shot.duration:g}秒（超出约{need - shot.duration:.1f}秒）；"
                    "请删减台词、拆分或延长镜头"
                )
        if speech_errors:
            raise ValueError("台词时长估算未通过：\n- " + "\n- ".join(speech_errors))

        return self


def main() -> int:
    if len(sys.argv) != 2:
        print("用法：python validate.py <storyboard.json>", file=sys.stderr)
        return 2

    path = Path(sys.argv[1])
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        storyboard = Storyboard.model_validate(data)
    except FileNotFoundError:
        print(f"错误：找不到文件：{path}", file=sys.stderr)
        return 2
    except json.JSONDecodeError as exc:
        print(
            f"JSON格式错误：第{exc.lineno}行，第{exc.colno}列：{exc.msg}",
            file=sys.stderr,
        )
        return 1
    except ValidationError as exc:
        for error in exc.errors():
            loc = ".".join(str(part) for part in error["loc"]) or "storyboard"
            print(f"校验失败 [{loc}]：{error['msg']}", file=sys.stderr)
        return 1
    except ValueError as exc:
        print(f"校验失败：{exc}", file=sys.stderr)
        return 1

    print(
        f"通过：{storyboard.title}，{len(storyboard.shots)}个镜头，"
        f"共{storyboard.total_duration:g}秒"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
