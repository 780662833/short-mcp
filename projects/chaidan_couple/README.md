# chaidan_couple storyboard

将已确认的《拆弹夫妻》分镜 JSON 保存为本目录下的 `storyboard.json`。当前 schema 面向 **分镜 → AI 素材生成**：`scene` 描述镜头内容，`prompt` 保存可直接提交给生成模型的提示词，`ref_image` 和 `seed` 为一致性与复现提供输入。

## 执行校验

```powershell
python -m pip install -r requirements.txt
python validate.py projects/chaidan_couple/storyboard.json
python -m unittest discover -s tests -v
```

## Shot 字段

| 字段 | 含义 |
|---|---|
| `id` | 镜头编号，从 1 开始连续递增 |
| `start` / `duration` | 镜头开始时间与持续秒数 |
| `scene` | 自然语言画面、动作、场景描述；可作为提示词生成的语义来源 |
| `dialogue` / `subtitle` / `sfx` | 对白、字幕和音效说明 |
| `clip` | 生成或拍摄完成后的素材文件路径 |
| `source` | 素材来源：`ai` 或 `shoot`，默认 `ai` |
| `prompt` | 传给视频/图像生成模型的镜头提示词；尚未生成时可留空 |
| `ref_image` | 角色参考图路径；尚未指定时可留空 |
| `seed` | 可选整数种子；未指定时为 `null` |

`source` 只允许 `ai` 或 `shoot`。当前 9 个镜头默认使用 `ai`，`prompt`、`ref_image` 暂留空，`seed` 暂为 `null`，不虚构尚未创建的提示词或参考图文件。

## 校验规则

- 镜头编号从 1 开始连续递增；
- 第一个镜头从 0 秒开始，后续镜头时间连续，允许 0.01 秒浮点误差；
- 所有镜头 duration 必须大于 0；
- 镜头时长之和必须与 total_duration 相符，允许 0.01 秒误差；
- 每条 subtitle 最多 12 个字符；
- 不接受 schema 未定义的字段；
- 默认按快节奏中文喜剧约 5 字/秒估算对白时长；每检测到一次换人说话，额外预留 0.3 秒；超时镜头会一起列出。

对白时长参数可通过环境变量调整，默认值不变：

```powershell
$env:SHORT_MCP_CHARS_PER_SEC = "5"
$env:SHORT_MCP_TURN_GAP = "0.3"
python validate.py projects/chaidan_couple/storyboard.json
```

- `SHORT_MCP_CHARS_PER_SEC`：每秒估算可说的有效字符数，必须大于 0；
- `SHORT_MCP_TURN_GAP`：每次换人说话额外预留的秒数，必须大于或等于 0。

对白时长只是生成前的估算，不等于实际配音时长。当前阶段只定义分镜数据契约与静态校验，不包含 Agent 自动重生成、自动错误回传或素材生成 API 调用。

## 提示词准备建议

把 `scene` 转为 `prompt` 时，保留人物身份、动作先后、景别、构图、环境、光线、风格与镜头运动；通过 `ref_image` 指向同一角色参考图，并按目标模型需要选择固定 `seed`。提示词应明确描述本镜头可见内容，不应凭空改变剧本中的关键动作或反转。
