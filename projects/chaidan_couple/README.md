# 拆弹夫妻：结构化分镜与素材任务

本项目将短视频生产输入拆成四份职责清晰的数据：

- `storyboard.json`：时间轴、镜头动作、构图、灯光、镜头运动、字幕和角色造型引用；
- `characters.json`：角色身份与不同 look 的统一定义；
- `style.json`：全局风格和负面提示词；
- `jobs/*.json`：每次生成尝试的提示词快照、参考图 SHA-256、seed、模型参数、素材与验收状态。

角色描述只维护在角色注册表里。提示词由固定顺序拼装：全局风格 → 景别/构图 → 角色身份/造型（按镜头声明顺序）→ 动作 → 灯光 → 运镜。提示词构建是确定性的，不调用任何生成模型。

## 安装与校验

```powershell
python -m pip install -r requirements.txt
python validate.py projects/chaidan_couple/storyboard.json projects/chaidan_couple
python -m unittest discover -s tests -v
```

当前角色 look 的 `ref_images` 为空数组，因为仓库尚未提供可验证的真实参考图。补入图片后，在对应 look 中填入相对项目目录的路径（例如 `refs/male_glam_front.png`）；素材校验会检查文件是否存在并计算 SHA-256。禁止使用越出项目目录的路径。

## MCP 工具

启动：`python mcp_server.py`

- `validate_assets(project)`：只读检查分镜、角色、look 和参考图，汇总错误；
- `prepare_generation_jobs(project, shot_ids?, model?, seed_support?)`：生成唯一任务单和稳定提示词快照，不调用生成 API；
- `attach_clip(project, shot_id, path)`：对已有视频运行 ffprobe，关联最新 prepared 任务；时长不足会返回警告；
- `review_clip(project, shot_id, verdict, note?)`：仅记录明确人工验收结果，verdict 为 `accepted` 或 `rejected`。

状态：`pending → prepared → generated → accepted/rejected`。拒绝后镜头状态回到 `prepared`，旧任务单仍保留；重新准备会创建新的任务单。素材挂接只登记现有文件，不会生成视频或覆盖媒体文件。

Seed 不能单独保证可复现。除非具体服务商明确保证确定性，否则应将其视为普通输入或 best-effort 设置。
