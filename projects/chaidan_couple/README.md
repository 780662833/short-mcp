# chaidan_couple storyboard

将已经确认的《拆弹夫妻》分镜 JSON 保存为本目录下的 `storyboard.json`。

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
- 按快节奏中文喜剧约 5 字/秒估算对白时长；每检测到一次换人说话，额外预留 0.3 秒；超时镜头会一起列出。

对白时长只是**生成前的估算门禁**，不等于实际录音、配音或表演时长。语气、停顿、重叠对白及情绪表演仍需通过配音/实拍或音频时长复核。

当前 `storyboard.json` 保留原始剧情内容；加入对白时长门禁后，预期会报告镜头 3、4、5、6、8 超时。这是有意暴露的待修问题，不应通过放宽门禁来让原稿“假通过”。

## 失败后的处理

把完整报错反馈给生成分镜的 Agent，并要求它按 `PROMPT_RULES.md` 重新生成；不要为了通过校验而手工改写剧情 JSON。
