# Issue #50：架构迁移收口

状态：执行中，P0-P5 已完成，P6a 已完成，P6b-P6d 待进入。

本变更将沿现有 Feature Ownership 方向完成 Composition Root、兼容桥、Worker、领域代码归属、Character Parser 和终态架构测试收口。实施保持用户行为、输出目录、数据库、日志、信号、取消和失败语义不变。

详细阶段计划、调查证据和验证结果见 `docs/worklog/issue-50-architecture-finalization/`。

本阶段新增 Audio Feature 的 Qt-free 处理器和 Worker 适配器；旧音频 Worker 路径仅保留兼容转发，未改变音频输出、分类、增量缓存、取消和失败信号契约。

本阶段新增 Importer Feature 的 Qt-free AssetStudio 导入处理器和 Worker 适配器；旧 `ImportASWorker` 路径仅保留兼容转发，未改变导入结果、分类提交/回滚、Lua 发布、精准 Bundle 筛选、取消和失败信号契约。

P6a 将音频目录/仓库/专辑映射和预览目录/Prefab 解析的真实实现归入对应 Feature；旧 `app/core` 路径仅保留兼容转发，未改变产物、分类、未读状态或预览匹配行为。
