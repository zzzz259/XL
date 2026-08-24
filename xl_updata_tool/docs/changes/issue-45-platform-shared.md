# Issue #45 变更摘要

## 面向评审

- Platform 稳定入口：路径、文件、进程、数据库和诊断。
- Shared Qt 稳定入口：主题令牌、页头、命令栏、空状态和状态标签。
- Feature 页面和控制器切换公共入口；旧实现保留兼容。
- 新增 ownership 验收，明确普通改动应落在单一 Feature 目录，公共区改动需独立 Issue。

## 不在本次范围

- 不删除旧 `app/core` 与 `app/ui` 实现。
- 不重写 Spine、FFmpeg、FGUI、debank 或 Lua 解析算法。
- 不改变 output/data/log、数据库、信号和取消契约。
