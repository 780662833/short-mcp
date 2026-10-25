import json
import sys
from pathlib import Path

from pydantic import ValidationError

from schemas import Storyboard, speech_need


def main() -> int:
    if len(sys.argv) not in (2, 3):
        print("用法：python validate.py <storyboard.json> [project_dir]", file=sys.stderr)
        return 2
    path = Path(sys.argv[1])
    try:
        storyboard = Storyboard.model_validate_json(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        print(f"错误：找不到文件：{path}", file=sys.stderr)
        return 2
    except json.JSONDecodeError as exc:
        print(f"JSON格式错误：第{exc.lineno}行，第{exc.colno}列：{exc.msg}", file=sys.stderr)
        return 1
    except ValidationError as exc:
        for error in exc.errors():
            loc = ".".join(str(part) for part in error["loc"]) or "storyboard"
            print(f"校验失败 [{loc}]：{error['msg']}", file=sys.stderr)
        return 1
    print(f"通过：{storyboard.title}，{len(storyboard.shots)}个镜头，共{storyboard.total_duration:g}秒")
    if len(sys.argv) == 3:
        from generation import validate_assets
        report = validate_assets(sys.argv[2])
        for issue in report["errors"]:
            print(f"素材错误：{issue}", file=sys.stderr)
        for warning in report["warnings"]:
            print(f"警告：{warning}", file=sys.stderr)
        if not report["ok"]:
            return 1
        print(f"素材校验通过：{len(report['checked_refs'])}张参考图已核验")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
