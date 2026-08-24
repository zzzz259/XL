# Issue #50：架构迁移收口

状态：终态门禁修正已完成，等待用户集中实机验收与 Sol 复核；未推送、未创建 PR。

本变更将沿现有 Feature Ownership 方向完成 Composition Root、兼容桥、Worker、领域代码归属、Character Parser 和终态架构测试收口。实施保持用户行为、输出目录、数据库、日志、信号、取消和失败语义不变。

详细阶段计划、调查证据和验证结果见 `docs/worklog/issue-50-architecture-finalization/`。

本阶段新增 Audio Feature 的 Qt-free 处理器和 Worker 适配器；旧音频 Worker 路径仅保留兼容转发，未改变音频输出、分类、增量缓存、取消和失败信号契约。

本阶段新增 Importer Feature 的 Qt-free AssetStudio 导入处理器和 Worker 适配器；旧 `ImportASWorker` 路径仅保留兼容转发，未改变导入结果、分类提交/回滚、Lua 发布、精准 Bundle 筛选、取消和失败信号契约。

P6a 将音频目录/仓库/专辑映射和预览目录/Prefab 解析的真实实现归入对应 Feature；旧 `app/core` 路径仅保留兼容转发，未改变产物、分类、未读状态或预览匹配行为。

P6b-版本将版本仓库、差异、下载计划、更新持久化、本地同步和种子实现归入 Versions Feature；旧 `app/core/version_*` 等路径仅保留兼容转发，Bundle Parser 仍留待下一批处理。

P6b-Bundle 将 Bundle Parser、Selector、Manager 分别归入 platform、Importer、Versions；旧 `app/core/bundle_*` 路径仅保留兼容转发，未改变 Bundle 修复、精准筛选和版本差异行为。

P6c 将角色缓存、展示/CSV、Wiki Profile 和增量仓库实现归入 Characters Feature；`character_loader.py` 留待 P7，未改变角色解析入口或数据结构。

P6d 将数据库、文件、路径、进程、下载和诊断实现归入 `app/platform/`；`app/core` 对应路径保留兼容转发，未改变日志、数据库、运行模式或外部进程契约。

P7 将角色 Lua 解析器按通用参数、基础表、养成消耗、技能、BaseCard 聚合和整体装配归入 `app/features/characters/parser/`；`CharacterService` 保持原稳定入口，旧 `app/core/character_loader.py` 仅保留兼容转发。新增脱敏 fixture 和旧实现深度等价保护，未改变角色数据结构、排序、进度回调或输出契约。

P8 将 Preview Worker/适配器/FGUI/专属对话框/控件归入 Preview Feature，将角色 Wiki 控件归入 Characters Feature，将页面壳层归入 Shared；Lua 成品仓库归入 Platform。Shell 改用 Feature Runtime 和通用 contribution，`app/core` 零引用后删除全部纯转发模块；三个旧 View 在引用归零后删除，仅保留明确登记的 `character_view.py` 兼容入口，未改变用户可见行为、输出、数据库、日志或取消语义。

终态复核修正：新增架构门禁，禁止 Shell 按业务 key 下转具体 Feature 实现，禁止 Feature 间导入内部实现；音频专辑映射改用 `app/shared/lua.py`。当前外部 basetemp 全量测试为 146 collected / 146 passed，Ruff、AST、import smoke、临时 Qt smoke、终态复现器和 diff check 均通过。
