# Issue #50：架构迁移收口调查

## 基线范围

- 需求：`XL-20260824-001-architecture-finalization`
- 总 Issue：#50
- 实施分支：`refactor/issue-50-architecture-finalization`
- 实施基线：从最新 `origin/main` 创建；不得使用调查快照 `7cc372c`。

## 已确认边界

- 本任务只做 Feature Ownership 终态迁移和行为保护，不做视觉重设计、不顺手修业务 Bug。
- `output/`、`data/`、`logs/`、数据库、缓存、信号、取消和失败保留语义属于稳定契约。
- 旧入口只有在 tracked 代码、测试、工具、脚本和正式文档引用归零后才能删除；否则保留薄兼容门面并登记调用方。

## 待采集事实

- 已采集环境：Python 3.12.6、PySide6 6.11.1、pytest 8.4.2、Ruff 0.16.3。
- 已采集规模：`main_window.py` 1243 行、`character_loader.py` 1365 行、tracked Python 156 个、tracked 测试文件 33 个、pytest 收集 127 项。
- 实施基线提交：`adf0a05941f0ebf0ee55a5d3e68a9880c1d69a51`（最新 `origin/main`）。
- 生产入口 `main.py` 仍直接导入并创建 `app.ui.main_window.MainWindow`；`app/bootstrap/app_factory.py` 目前只是迁移期注册骨架。
- MainWindow 仍直接导入并装配 Audio、Characters、Versions、Importer、Preview 具体类型；这是 P2/P3 的计划内迁移对象。
- Preview 导出编排仍使用 `parent._...` 私有字段，旧 `app/ui/views/*` 仍公开 `controls_dict`；这是 P1 的计划内迁移对象。
- `app/features/audio/worker.py`、`importer/worker.py`、`preview/worker.py`、`versions/worker.py` 仍引用 `app.ui.workers`；这是 P4/P5 的计划内迁移对象。
- 旧领域实现和兼容入口仍有 tracked 引用，当前不能删除；后续以逐文件引用归零清单为准。
- 完整的旧层文件、直接引用关系、`controls_dict`/`parent._` 事实和删除前复核命令已固化在同目录 `references.md`；当前清单不是删除授权。

## P0 自动门禁基线

- 全量 pytest：127 项收集，全部通过。
- Ruff：`ruff check --no-cache app tests` 通过。
- AST：`AST_OK 149`。
- import smoke：使用根入口 `main.py`、bootstrap 和各 Feature 包，`IMPORT_SMOKE_OK`。
- Qt offscreen smoke：`QT_SMOKE_OK`，默认版本页可见，Preview/Audio/Characters 默认隐藏。
- 生产启动、导航、页面 objectName/信号、ImportResult、Worker 取消和 Character Parser 行为已有现有测试覆盖；本阶段先以这些现有测试作为保护网，不重复制造同义测试。
- P0 未新增生产代码，也未把未来终态架构约束提前写成会阻断当前迁移态的测试；终态约束按 P8 在替代入口完成后升级。

## P0 退出复核（2026-08-24）

- 全量 `pytest -q`：退出码 0，127 项全部通过；仅有既存 `.pytest_cache` 无写权限警告。
- `ruff check --no-cache app tests`：退出码 0，`All checks passed!`。
- 不落盘 AST 解析：`AST_OK 149`。
- 根入口、bootstrap 和全部 Feature import smoke：`IMPORT_SMOKE_OK`。
- Qt offscreen smoke（授权环境）：`QT_SMOKE_OK True False False False`，版本页默认可见，预览/音频/角色页默认隐藏。
- `git diff --check`：`DIFF_CHECK_OK`。
- `compileall` 未作为通过依据：它只因尝试写入已有拒绝访问的 `__pycache__` 失败；不修改缓存权限，已由 AST 解析替代源码语法门禁。

## 基线扫描中的探针错误

- 初次文件行数命令在仓库根目录误使用了 `app/...` 相对路径，得到不存在路径；已用 `xl_updata_tool/app/...` 重跑并取得正确数值。
- 初次 import smoke 使用了不存在的 `app.main`；已改为导入根入口 `main` 后通过。
- 初次 Qt smoke 读取了不存在的 `view_stack` 属性；已改为读取现有页面可见性属性后通过。
- 一次 `rg` 命令携带 Windows 不支持的 `tests/test_*_feature.py` 通配参数；不影响扫描结果，改用目录扫描完成核对。
