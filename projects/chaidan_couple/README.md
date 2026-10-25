# chaidan_couple storyboard

将已确认的《拆弹夫妻》分镜 JSON 保存为本目录下的 `storyboard.json`。

## 执行校验

```powershell
python -m pip install -r requirements.txt
python validate.py projects/chaidan_couple/storyboard.json
```

运行校验器单元测试：

```powershell
python -m unittest discover -s tests -v
```

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

对白时长只是**生成前的估算门禁**，不等于实际录音、配音或表演时长。语气、停顿、重叠对白及情绪表演仍需通过配音/实拍或音频时长复核。

## 失败后的处理

把完整报错反馈给生成分镜的 Agent，并要求它按 `PROMPT_RULES.md` 重新生成；不要为了通过校验而手工改写剧情 JSON。仅当用户明确提供新版剧本时，才用该版本更新 `storyboard.json`。
