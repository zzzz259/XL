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
- Preview 导出编排已迁入 Feature-owned Controller 状态；旧 `app/ui/views/preview_view.py` 仍公开 `controls_dict`，仅作为尚有测试/兼容调用方的保留门面。
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

## P1 退出复核（2026-08-24）

- `PreviewPage` 已直接构造并持有页面控件，不再调用旧 `create_preview_view()`，不再暴露 `controls` 字典。
- `PreviewController` 已接管页面筛选、加载进度、重载角色选择、上下文菜单、双击图片、文件定位/复制和 GIF/视频导出编排。
- `MainWindow` 已删除 Preview 控件镜像、预览加载/导出回调、上下文菜单、双击和文件操作实现；只保留页面切换和通用状态栏桥接。
- 导出模块改为接收 `PreviewController`，其 worker、页面按钮、骨架映射和状态均由 Feature 自己持有；不再读取宿主窗口私有字段。
- 旧 `app/ui/views/preview_view.py` 仍被现有测试和兼容入口使用，登记为保留门面，未删除。
- P1 全量 `pytest -q`：127 项全部通过。
- `ruff check --no-cache app tests`：通过。
- 不落盘 AST：`AST_OK 149`。
- Qt offscreen：`QT_SMOKE_OK True False False False False`；最后一项确认 `MainWindow.preview_controls` 不存在。
- `git diff --check`：通过。

## P3 退出复核（2026-08-24）

- 生产入口已切换为：日志配置 → QApplication → `build_app_context()` → `create_application_runtime()` → `MainWindow(runtime=...)`；日志初始化仍早于业务窗口导入。
- MainWindow 不再导入或构造具体 Feature Page/Controller/Service；只从 Runtime descriptor 取得 page/controller，并保留现有 Shell 状态栏、导入进度和窗口生命周期。
- 顶部导航由 Runtime descriptor 生成，Importer 作为无页面 Feature 不进入导航；既有四个用户可见入口顺序和文案由工厂 descriptor 固化。
- 版本、预览、音频、角色页面切换统一经过 `FeatureRuntimeRegistry.activate()`；首次加载动作仍由 Shell 的通用激活入口桥接，未改变现有数据加载时机。
- `ImportPostprocessWorkflow` 由 Composition Root 创建并显式接收 Importer/Audio/Characters；ImportResult 的音频后处理、取消/失败和 Lua 后处理路由不再由 MainWindow 判断。
- P3 全量 `pytest -q`：159 项收集，全部通过；`ruff check --no-cache app tests`、不落盘 AST `AST_OK 159`、import smoke、runtime Qt smoke `RUNTIME_QT_SMOKE_OK True False False False 4`、diff check 均通过。

## P2 退出复核（2026-08-24）

- 新增 `features/{versions,preview,audio,characters,importer}/factory.py`，集中描述页面、Controller、Service 和路径装配；五个 Feature 的 descriptor 顺序固定为版本、预览、音频、角色、导入。
- `app/bootstrap/app_factory.py` 新增 `default_feature_definitions()`；生产入口暂不切换，保留迁移期旧装配作为行为对照。
- `app/bootstrap/runtime.py` 新增 Qt-free `FeatureRuntimeRegistry`，统一提供 descriptor 查找、页面激活、状态/进度/角标 binding 和逆序资源关闭。
- `FeatureRuntime` 增加 opaque 的 `status_signal`、`progress_signal`、`badge_signal`，shared/bootstrap 不导入 Qt。
- P2 工厂隔离测试、Runtime registry 测试、全量 pytest：156 项收集，全部通过。
- `ruff check --no-cache app tests`：通过；不落盘 AST：`AST_OK 156`；import smoke：`IMPORT_SMOKE_OK`；Qt offscreen：`QT_SMOKE_OK True False False False`；diff check：通过。

## P4 实施记录（2026-08-24）

- `app/features/audio/processing.py` 已接管原音频 Worker 中的 Qt-free 处理：bytes/bank 筛选、debank 调用、Lua 专辑分类、最终路径索引、语音文件名归一化、旧分类清理、临时目录清理和 BGM 审计。
- `app/features/audio/worker.py` 现在只负责 QThread 生命周期、取消请求、处理器回调和既有 `progress`/`finished_decrypt`/`cancelled_decrypt`/`error` 信号映射；AudioController 的现有信号契约未改变。
- `app/ui/workers/audio_decrypt.py` 改为单一实现的薄兼容入口；没有删除它，因为旧测试/脚本入口仍受保护。
- P4 退出门新鲜证据：`pytest -q -p no:cacheprovider` 通过（133 项）；Ruff 通过；不落盘 AST `AST_OK 160`；import smoke `IMPORT_SMOKE_OK`；runtime Qt smoke `RUNTIME_QT_SMOKE_OK ('versions', 'preview', 'audio', 'character', 'importer')`；`git diff --check` 通过。首次 Qt smoke 的数据库权限失败已用任务临时目录复跑通过，未修改仓库权限。

## 基线扫描中的探针错误

- 初次文件行数命令在仓库根目录误使用了 `app/...` 相对路径，得到不存在路径；已用 `xl_updata_tool/app/...` 重跑并取得正确数值。
- 初次 import smoke 使用了不存在的 `app.main`；已改为导入根入口 `main` 后通过。
- 初次 Qt smoke 读取了不存在的 `view_stack` 属性；已改为读取现有页面可见性属性后通过。
- 一次 `rg` 命令携带 Windows 不支持的 `tests/test_*_feature.py` 通配参数；不影响扫描结果，改用目录扫描完成核对。

## P5 退出复核（2026-08-24）

- `app/features/importer/processing.py` 已接管 Bundle 修复、AssetStudio 资源映射、分类导出、staging 提交/回滚、Lua 发布、精准 Bundle 输入和取消检查。
- `app/features/importer/worker.py` 已收回 Importer Worker ownership，只把 Qt 线程生命周期、取消、进度、阶段、分类和完成回调映射为既有信号。
- 旧 `app/ui/workers/import_as.py` 保留为 `ImportASWorker` 薄兼容门面；生产 Feature 不再反向引用旧路径。
- P5 新鲜验证：全量 `pytest -q -p no:cacheprovider` 通过（134 项）；Ruff 通过；不落盘 AST `AST_OK 161`；Importer import smoke `IMPORTER_IMPORT_SMOKE_OK`；runtime Qt smoke `RUNTIME_QT_SMOKE_OK ('versions', 'preview', 'audio', 'character', 'importer')`；`git diff --check` 通过。

## P6a 退出复核（2026-08-24）

- 音频领域真实实现已归入 `app/features/audio/{audio_library,audio_repository,album_map}.py`；预览目录和 Prefab 解析已归入 `app/features/preview/{catalog,prefab_parser}.py`。
- `app/core/` 对应五个旧路径改为显式迁移期转发；Audio/Preview Feature 内部 import 已切到 Feature 路径，测试和预览导出调用方同步切换。
- P6a 未删除兼容门面：`git grep` 仍能发现旧入口被测试、Shell、旧 UI 或兼容模块使用；引用归零留到 P8 的清理门。
- P6a 新鲜验证：全量 `pytest -q -p no:cacheprovider` 通过（135 项）；Ruff 通过；不落盘 AST `AST_OK 166`；`P6_IMPORT_SMOKE_OK`；runtime Qt smoke `RUNTIME_QT_SMOKE_OK ('versions', 'preview', 'audio', 'character', 'importer')`；`git diff --check` 通过。

## P6b-版本 退出复核（2026-08-24）

- `app/features/versions/` 已接管 `version_cleanup`、`version_data`、`version_download`、`version_manager`、`version_update`、`local_bundle_sync` 和 `seed_versions` 的真实实现；Version Service/Controller 已切到 Feature 内部路径。
- 对应 `app/core/version_*`、`app/core/local_bundle_sync.py`、`app/core/seed_versions.py` 均保留为迁移期兼容转发；Bundle Parser 仍暂由旧 core 兼容入口提供，留待下一批处理。
- P6b-版本 新鲜验证：全量 `pytest -q -p no:cacheprovider` 通过（136 项）；Ruff 通过；不落盘 AST `AST_OK 173`；`VERSIONS_IMPORT_SMOKE_OK`；runtime Qt smoke `RUNTIME_QT_SMOKE_OK ('versions', 'preview', 'audio', 'character', 'importer')`；`git diff --check` 通过。

## P6b-Bundle 退出复核（2026-08-24）

- Bundle Parser 真实实现已归入 `app/platform/bundle_parser.py`，Bundle Selector 归入 `app/features/importer/bundle_selector.py`，Bundle Manager 归入 `app/features/versions/bundle_manager.py`。
- Versions 的差异/种子和 Importer 的导入处理已切到新边界；旧 `app/core/bundle_*.py` 只保留兼容转发，Shell/旧 Worker 仍可通过原入口工作。
- 首次 import smoke 发现移动后的 `bundle_manager.py` 遗留 `.logger` 相对导入；已定位并切到 `app.platform.diagnostics`，之后 import、pytest、Ruff、AST、Qt smoke 和 diff 全部重跑通过。
- P6b-Bundle 新鲜验证：全量 `pytest -q -p no:cacheprovider` 通过（137 项）；Ruff 通过；不落盘 AST `AST_OK 176`；`BUNDLE_IMPORT_SMOKE_OK`；runtime Qt smoke `RUNTIME_QT_SMOKE_OK ('versions', 'preview', 'audio', 'character', 'importer')`；`git diff --check` 通过。

## P6c 退出复核（2026-08-24）

- `app/features/characters/` 已接管角色缓存索引、Wiki 展示/CSV、Profile 模型和增量仓库；Character Service/Controller/详情组件与测试已切到 Feature 路径。
- `character_loader.py` 仍按计划保留在 `app/core`，作为 P7 解析器拆分对象；本批未复制或改写 Lua 解析实现。
- 对应 `app/core/character_cache.py`、`character_presenter.py`、`character_profile.py`、`character_repository.py` 保留兼容转发；旧入口仍有测试/组件兼容调用，未执行删除。
- P6c 新鲜验证：全量 `pytest -q -p no:cacheprovider` 通过（138 项）；Ruff 通过；不落盘 AST `AST_OK 180`；`CHARACTERS_IMPORT_SMOKE_OK`；runtime Qt smoke `RUNTIME_QT_SMOKE_OK ('versions', 'preview', 'audio', 'character', 'importer')`；`git diff --check` 通过。

## P6d 退出复核（2026-08-24）

- 数据库、文件、路径、进程、下载、日志上下文、任务上下文、环境摘要、崩溃报告和运行模式配置的真实实现已归入 `app/platform/`；`app/platform/diagnostics.py` 统一提供诊断契约。
- `app/core` 对应路径保留兼容转发，应用内部新引用已切到 platform；平台实现不再依赖这些 core 兼容模块。
- 首次提交准备时发现 Git 命令工作目录前缀错误，产生了临时 compat 文件且未带入 core 转发；已立即建立修正检查点 `19f0268`，恢复兼容入口并删除临时文件，修正后验证通过。
- P6d 新鲜验证：全量 `pytest -q -p no:cacheprovider` 通过（139 项）；Ruff 通过；不落盘 AST `AST_OK 187`；兼容 import `COMPAT_IMPORT_SMOKE_OK`；runtime Qt smoke `RUNTIME_QT_SMOKE_OK ('versions', 'preview', 'audio', 'character', 'importer')`；`git diff --check` 通过。

## P7 角色 Lua 解析器拆分（2026-08-24）

- 原 `app/core/character_loader.py` 的实现已迁移到 `app/features/characters/parser/`，按职责拆为 `common.py`、`words.py`、`progression.py`、`skills.py`、`cards.py` 和 `assembler.py`；`assembler.py` 保持 `load_character_data()`、返回结构和进度回调语义。
- `CharacterService`、音频专辑映射和角色解析测试已切到 Feature Parser 公共入口；`app/core/character_loader.py` 仅保留 re-export 兼容入口，不维护第二份实现。
- 新增脱敏的最小 Lua fixture 和完整结果形状测试，覆盖文本、CV、等级/品质属性、技能、角色卡、语音、故事、徽章和消耗装配；另用 `19f0268` 中的旧实现对同一临时 fixture 做深度等价核对，结果为 `P7_GOLDEN_EQUIVALENCE_OK`。
- 解析器模块不依赖 PySide6/PyQt；无真实游戏数据进入版本库。
