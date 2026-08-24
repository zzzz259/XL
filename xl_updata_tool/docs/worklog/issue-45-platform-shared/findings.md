# Issue #45 调查结论

## 当前边界

- `app/core` 仍承载路径、数据库、日志、外部进程和诊断实现；直接依赖分散在 Shell、Feature 和旧 Worker 中。
- `app/ui/theme.py` 与 `app/ui/widgets/view_chrome.py` 已形成稳定的通用 UI 能力，但 Feature 页面仍直接依赖旧路径。
- 删除旧实现会扩大本轮风险，因此本阶段采用稳定入口 + 兼容底层，不改变输出目录、数据库、日志、Qt 信号和取消语义。

## 目标边界

- Platform 负责环境、路径、文件、进程、数据库和诊断契约。
- Shared Qt 负责主题令牌和通用页面壳层；业务控件仍归属具体 Feature。
- Feature ownership 由目录、静态门禁和 Issue 记录共同约束，避免每次修改都触碰 `main_window.py`。
