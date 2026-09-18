# 图片预览工作台验收记录（2026-09-19）

## 自动验证

- 项目 Python：`E:\All-Projects\XL\.venv\Scripts\python.exe`
- 完整测试套件：通过
- 相关预览/导出回归测试：通过
- Ruff：通过
- `compileall`：通过
- `git diff --check`：通过

## 真实资源验证

- 使用真实 `cardspine_10080_4.skel` 与 `cardspine_10080_4_bg.skel` 及其 atlas 执行 SpineViewerCLI merge。
- 角色与背景在同一次调用中导出，CLI 返回码为 0。
- 默认 MP4 命令不传入 `--duration`，使用 SpineViewerCLI 的自动完整动画时长行为。
- 临时验收视频生成成功后已删除，未写入项目 `output/`。

## 关键输出契约

```text
output/character/<角色ID>/<角色立绘>.png
output/character/<角色ID>/<角色立绘>.mp4
```

每个成品旁保留独立的 `<文件名>.metadata.json`，避免同目录下 PNG 与 MP4 互相覆盖元数据。
