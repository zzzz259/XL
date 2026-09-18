# 调查发现

- MP4 被写入独立目录的直接原因是默认导出计划把视频根目录硬编码为 `character_root.parent / "video"`；默认计划现改为与 PNG 共用 `character_root`。
- 视频被截断的直接原因是控制器查询到动画时长后，把该值写入 `ExportSettings.duration`，命令构造器随后显式传入 `--duration`。SpineViewerCLI 的 `merge` 默认值为 `--duration -1`，表示自动完整动画；内置默认视频现使用 `duration=None` 并省略该参数。
- 自定义导出仍保留显式 duration，因此高级用户可以继续控制传统导出行为。
- 真实验收确认角色和背景仍通过一次 `merge` 调用完成，未退回分开渲染再拼接。
