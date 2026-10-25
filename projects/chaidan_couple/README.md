# chaidan_couple storyboard

将已经确认的《拆弹夫妻》分镜 JSON 保存为本目录下的 `storyboard.json`。

执行校验：

```powershell
python -m pip install -r requirements.txt
python validate.py projects/chaidan_couple/storyboard.json
```

校验规则：
- 镜头编号从 1 开始连续递增；
- 第一个镜头从 0 秒开始，后续镜头时间连续，允许 0.01 秒浮点误差；
- 所有镜头 duration 必须大于 0；
- 镜头时长之和必须与 total_duration 相符，允许 0.01 秒误差；
- 每条 subtitle 最多 12 个字符；
- 不接受 schema 未定义的字段。

如果校验失败，优先把错误反馈给生成分镜的提示词/Agent，让它重新生成；不要为通过校验而手工改动剧情 JSON。
