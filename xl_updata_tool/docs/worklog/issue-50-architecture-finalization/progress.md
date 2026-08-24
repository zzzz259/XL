# Issue #50：执行进度

## 2026-08-24

- [completed] 读取需求和 approved_for_execution 交接包。
- [completed] 获取最新 `origin/main`，确认 PR #48/#49 已合并。
- [completed] 创建总 Issue #50 和长期任务分支 `refactor/issue-50-architecture-finalization`。
- [completed] 建立 P0 行为保护网和执行前基线证据：pytest 127 项、Ruff、AST 149、import smoke、Qt offscreen smoke 均通过。
- [completed] 将 P0 基线、现有行为保护范围和 tracked 引用归零清单固化到 worklog；终态架构约束保留到对应迁移阶段升级，避免提前锁死迁移态。
- [completed] P0 退出复核：pytest、Ruff、AST、import smoke、授权 Qt smoke 和 diff check 通过；compileall 的缓存写权限问题已记录，不影响源码门禁。
- [completed] P1：PreviewPage、PreviewController 和导出编排已收回 Feature ownership；页面行为保护网、全量门禁和 Qt smoke 通过。
- [completed] P2：五个 Feature 工厂、opaque 状态/进度/角标端口和 Qt-free Runtime registry 已建立；生产装配暂未切换。
- [completed] P3：生产入口、descriptor 导航和 ImportResult 后处理 workflow 已切换；全量门禁和 runtime Qt smoke 通过。
- [completed] P4：迁移 Audio 处理 Worker；Qt-free 音频处理进入 Feature，生产 Feature Worker 不再依赖旧 `app/ui/workers/audio_decrypt.py`，旧路径保留薄兼容门面。
- [completed] P5：迁移 Importer / AssetStudio Worker；Qt-free 导入处理进入 Feature，生产 Feature Worker 不再依赖旧 `app/ui/workers/import_as.py`，旧路径保留薄兼容门面。
- [completed] P6a：归位音频与预览领域实现；`app/core` 对应路径降为兼容转发，Feature 内部不再从 core 获取这些领域实现。
- [completed] P6b-版本：归位版本仓库、差异、下载计划、更新持久化、本地 Bundle 同步和种子实现；对应 core 路径降为兼容转发。
- [completed] P6b-Bundle：归位 Bundle Parser/Selector/Manager，并收口 Versions/Importer 调用边界；旧 core 路径保留兼容转发。
- [completed] P6c：归位角色缓存、展示模型、Wiki Profile 和增量仓库实现；`character_loader.py` 留待 P7。
- [completed] P6d：收口平台与诊断基础设施；`app/core` 对应路径降为兼容转发，平台实现与诊断契约已稳定。
- [completed] P7：拆分角色 Lua 解析器；按通用参数、基础表、养成消耗、技能、BaseCard 聚合和整体装配归位，保留旧 core re-export 入口并加入脱敏 fixture/golden 保护。
- [completed] P8a：收回 Preview 的 Worker、适配器、FGUI、专属对话框和拖拽控件；Shared 页面壳层归位，Shell 的导入筛选、音频未读和版本日志改为稳定 Feature API。
- [completed] P8b：收回 Lua 成品仓库和剩余生产入口；完成 `app/core` 零引用清理，更新终态架构测试与使用/开发文档。

## 偏差

暂无。

## 终态复核退回后的修正（2026-08-24）

- [completed] 根据 Sol 复核意见补充终态架构测试，覆盖 Shell 具体 Feature 下转、跨 Feature 内部导入和旧 View 完整实现三类门禁。
- [completed] `MainWindow` 改为通过 Runtime 与通用 Shell contribution 接入功能；跨 Feature 的 Lua 文本解析归入 `app/shared/lua.py`。
- [completed] 基于 tracked 生产代码、测试、工具和正式文档真实引用重新核对，删除 `audio_view.py`、`preview_view.py`、`version_view.py`；未恢复兼容门面。
- [completed] 修正文档中的迁移态描述和旧 View 引用，补充当前终态引用结论。
- [completed] 新鲜全量测试为 146 collected / 146 passed；这是在 Sol 的 143 项复核基础上新增 3 项终态架构测试后的当前计数。Ruff、AST、import smoke、Qt offscreen smoke、终态复现器和 diff check 均通过。

## 当前状态

等待 Sol 对本次终态门禁修正独立复核，以及用户实机验收；在此之前不推送分支、不创建 PR。

本轮本地 checkpoint：`9828d80 refactor: 收口终态架构边界`。

## 纯文档净化（2026-08-24）

- [completed] 重排 `references.md`：P8 当前引用结论置于前部，P0 旧关系明确降为历史快照；当前兼容入口逐项登记真实 tracked 调用方。
- [completed] 修正正式架构文档中的 Importer、Preview、Shell 和兼容入口路径，移除已完成 P8 后仍存在的迁移态路线描述。
- [completed] 更新 `app/bootstrap/app_factory.py` 顶部文档字符串，使其描述当前 Composition Root 和 Shell contribution 事实；未改生产逻辑。
- [completed] 未覆盖交接包 `Sol 复核` 区域；本轮只在 Luna 执行结果和项目 worklog 留痕。
