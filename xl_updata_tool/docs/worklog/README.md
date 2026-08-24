# XL 工作记录索引

工作记录按 Issue 分目录保存，避免把一次性调查过程混入长期开发指南。

- `task_plan.md`：架构阶段索引与当前任务入口。
- `progress.md`：阶段完成状态摘要。
- `issue-<id>-<slug>/findings.md`：调查与边界结论。
- `issue-<id>-<slug>/progress.md`：Issue 执行进度。
- `issue-<id>-<slug>/verification.md`：验证门禁和兼容性记录。
- `../changes/issue-<id>-<slug>.md`：面向评审的变更摘要。

当前 Issue：

- `issue-45-platform-shared/`：Platform/Shared 与 ownership 收口。
- `issue-46-view-switch-layout/`：多页面切换时的版本页布局占位修复。
- `issue-47-audio-performance/`：音频后台预热、分层懒加载与稳定勾选。

用户使用说明只进入项目 README；当前有效的架构和开发约束只进入开发指南；废弃方案、调试过程和一次性验证不写入人类面向的产品文档。
