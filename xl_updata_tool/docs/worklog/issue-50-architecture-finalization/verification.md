# Issue #50：验证记录

## P0 退出门

- [x] 现有全量测试通过：127 项。
- [x] characterization 保护网由现有 Feature/UI/导入/音频/角色测试覆盖，并在未重构代码上通过。
- [x] AST compile/import smoke 通过：AST 149 个文件，根入口与 Feature import smoke 通过。
- [x] Qt offscreen smoke 通过：默认版本页可见，其余三页隐藏。
- [x] Ruff 覆盖全部首方 `app/` 与 `tests/` 并通过。
- [x] `git diff --check` 和初始文件范围检查通过。
- [x] 行为、引用和兼容面基线已记录：`findings.md` 与 `references.md`。
- [x] 已核对旧层当前仍有正式引用；未执行越界删除。

## 自动验证结果

基线证据详见 `findings.md` 与 `references.md`；后续每个阶段追加新鲜结果，不覆盖本次基线。

## P0 复核说明

本阶段没有生产实现改动。已有测试已经覆盖启动入口、页面可见性、导航信号、Feature 页面契约、导入结果、音频取消和角色解析等当前稳定行为；P0 只固化这些证据和删除前引用规则。终态架构测试将在替代入口实际落地后再收紧，避免把当前迁移态误判为终态。

## P0 退出复核证据（2026-08-24）

- `pytest -q`：127 passed，退出码 0。
- `ruff check --no-cache app tests`：通过。
- AST：149 个 Python 文件通过不落盘解析。
- import smoke：`IMPORT_SMOKE_OK`。
- Qt offscreen smoke：`QT_SMOKE_OK True False False False`。
- `git diff --check`：通过。
- `compileall` 受仓库已有 `__pycache__` 权限限制未通过；未修改权限，使用 AST 解析完成等价的源码语法门禁。

## P1 退出门

- [x] Preview 加载、筛选、选择、上下文菜单、双击、导出、取消、进度、错误和空状态已有针对性/全量测试保护。
- [x] PreviewPage 不再依赖旧 View 的 `controls` 字典。
- [x] MainWindow 不再拥有 Preview 控件镜像或 Preview Worker 状态。
- [x] 导出编排依赖 PreviewController 的显式状态，不再依赖宿主窗口私有字段。
- [x] 旧 `preview_view.py` 的保留原因与调用方已登记；未执行无证据删除。
- [x] P1 全量 pytest 127 passed、Ruff、AST 149、Qt offscreen 和 diff check 通过。

## P2 退出门

- [x] 五个 Feature 工厂可在隔离 `AppContext` 中创建。
- [x] `FeatureRuntimeRegistry` 覆盖顺序、重复 key、页面激活、status/progress/badge binding 和逆序关闭。
- [x] shared/bootstrap 保持 Qt-free；signal 作为 opaque port 传递。
- [x] P2 全量 pytest 156 passed、Ruff、AST 156、import smoke、Qt offscreen 和 diff check 通过。
- [x] 生产启动仍走旧装配路径，未把 P2 装配变化误认为用户行为变化；正式切换留到 P3。

## P3 退出门

- [x] 生产 `main.py` 显式创建 AppContext、ApplicationRuntime 并传入 MainWindow，日志配置顺序保持不变。
- [x] MainWindow 无具体 Feature import/constructor；导航从 descriptor 生成。
- [x] ImportResult → Audio/Characters 后处理由 Composition Root workflow 显式路由，覆盖成功、取消和音频失败路径。
- [x] 159 项全量 pytest、Ruff、AST 159、import smoke、runtime Qt offscreen smoke 和 diff check 通过。
- [x] 既有 MainWindow 作为迁移期公共入口仍可无 runtime 参数启动，兼容旧测试/脚本调用。

## P4 退出门

- [x] Audio processing 不依赖 PySide6，Feature Worker 不依赖旧 `app.ui.workers.audio_decrypt`。
- [x] 旧音频入口保留为薄兼容转发，没有第二份处理实现；删除前的引用归零条件仍未满足。
- [x] bytes/bank 筛选、debank、分类、增量路径索引、语音命名修正、旧产物清理、BGM 审计和取消检查均保留在单一处理器中。
- [x] 音频处理、Audio Feature、Audio Repository、专辑映射、epic7_debank 和架构边界针对性测试通过。
- [x] P4 全量 pytest 133 passed、Ruff、AST 160、import smoke、runtime Qt smoke 和 diff check 通过；首次 Qt smoke 的真实数据库目录权限失败已改用任务临时目录复核通过。

## P5 退出门

- [x] Importer processing 不依赖 PySide6，Feature Worker 不依赖旧 `app.ui.workers.import_as`。
- [x] Bundle 修复、AssetStudio 映射、分类导出、staging 提交/回滚、Lua 发布、精准 Bundle 输入和取消语义由单一处理器承载。
- [x] 旧导入入口保留为薄兼容门面，没有第二份实现；删除前的引用归零条件仍未满足。
- [x] 全量 pytest 134 passed、Ruff、AST 161、Importer import smoke、runtime Qt smoke 和 diff check 通过。

## P6a 退出门

- [x] 音频目录、快照、专辑映射和预览目录/Prefab 解析的真实实现已进入对应 Feature。
- [x] `app/core` 旧路径仅保留兼容转发；Feature 内部 import 已切换到新路径。
- [x] 兼容调用方仍有登记，未执行无证据删除。
- [x] 全量 pytest 135 passed、Ruff、AST 166、P6 import smoke、runtime Qt smoke 和 diff check 通过。

## P6b-版本退出门

- [x] Versions Feature 已拥有版本仓库、差异、下载计划、更新持久化、本地同步和种子实现。
- [x] Version Service/Controller 不再从 `app.core.version_*`、`local_bundle_sync`、`seed_versions` 获取领域实现。
- [x] 旧 core 路径保留兼容转发；Bundle Parser 依赖尚未在本批删除或伪装为已归位。
- [x] 全量 pytest 136 passed、Ruff、AST 173、Versions import smoke、runtime Qt smoke 和 diff check 通过。

## P6b-Bundle 退出门

- [x] Bundle Parser、Selector、Manager 已按消费边界进入 platform、Importer、Versions。
- [x] Versions/Importer 内部不再从 `app.core.bundle_*` 获取实现；旧 core 路径保留兼容转发。
- [x] 移动后的相对 logger 导入问题已修复并通过 import smoke。
- [x] 全量 pytest 137 passed、Ruff、AST 176、Bundle import smoke、runtime Qt smoke 和 diff check 通过。

## P6c 退出门

- [x] 角色缓存、展示/CSV、Profile 和增量仓库真实实现已进入 Characters Feature。
- [x] Character Service/Controller/详情组件不再从 `app.core.character_*` 获取这些领域实现。
- [x] `character_loader.py` 未在本批移动，P7 解析器拆分边界保持清晰；旧 core 兼容入口未删除。
- [x] 全量 pytest 138 passed、Ruff、AST 180、Characters import smoke、runtime Qt smoke 和 diff check 通过。

## P6d 退出门

- [x] 数据库、文件、路径、进程、下载和诊断真实实现已进入 `app/platform/`。
- [x] 平台实现不依赖对应 `app.core` 兼容模块；旧 core 路径保留兼容转发。
- [x] 临时 compat 文件误入暂存的问题已用修正检查点恢复并验证。
- [x] 全量 pytest 139 passed、Ruff、AST 187、兼容 import smoke、runtime Qt smoke 和 diff check 通过。

## P7 退出门

- [x] `app/features/characters/parser/` 已按职责包含 common、words、progression、skills、cards、assembler；各模块无 Qt 依赖。
- [x] Character Service、音频专辑映射和角色解析测试已切到 Feature Parser；`app/core/character_loader.py` 仅为兼容 re-export。
- [x] 脱敏 fixture 完整形状测试通过；旧实现与同一临时 fixture 深度等价核对通过：`P7_GOLDEN_EQUIVALENCE_OK`。
- [x] 针对性测试、全量 pytest 141 项、Ruff、AST `AST_OK 193` 通过；删除/引用检查确认生产代码不再依赖旧 core 实现。
- [x] `P7_IMPORT_SMOKE_OK`、`RUNTIME_QT_SMOKE_OK ('versions', 'preview', 'audio', 'character', 'importer')` 和 `git diff --check` 通过。
- [x] 本阶段源码引用检查确认 `app`、`tests`、`tools`、`scripts` 不再直接依赖 `app.core.character_loader`；兼容入口仍可导入。

## P8 退出门

- [x] Preview Feature 不再依赖 `app.ui.workers`、`app.ui.adapters`、`app.ui.dialogs` 或 `app.ui.widgets`；Preview 专属实现、Shared 壳层和角色详情控件均已归位，旧路径仅保留薄转发。
- [x] Importer/Versions Worker 实现不再依赖旧 UI Worker；Lua 成品仓库位于 `app/platform/lua_repository.py`，应用内部不再依赖 `app.core`。
- [x] `app/core` 下全部纯转发 Python 文件已在引用归零后删除；终态测试强制确保无 Python 模块残留。
- [x] 全量 pytest 180 项、Ruff、AST `AST_OK 179`、`P8_IMPORT_SMOKE_OK`、runtime Qt smoke `RUNTIME_QT_SMOKE_OK ('versions', 'preview', 'audio', 'character', 'importer')` 和 core import zero 检查通过。
- [x] 针对性测试曾发现旧测试 patch 迁移前内部变量，已按稳定 Controller API 修正并重新通过；未改变产品行为。

## P8 首次终态复核快照（已被后续修正证据取代）

- [x] 独立复跑 `pytest -q`：180 项通过；仅出现仓库既有 `.pytest_cache` ACL 警告，未修改权限。
- [x] 独立复跑 `ruff check --no-cache app tests`：通过。
- [x] 不落盘 AST 复核：`AST_OK 183`；Feature/Platform 导入 smoke：`P8_IMPORT_SMOKE_OK`。
- [x] `app`、`tests`、`tools` 中实际 `from/import app.core` 引用归零，`app/core` 无 Python 模块：`P8_CORE_IMPORT_ZERO_OK`。
- [x] 使用临时运行时目录执行 Qt offscreen Feature Runtime 装配：`RUNTIME_QT_SMOKE_OK ('versions', 'preview', 'audio', 'character', 'importer')`。
- [x] `git diff --check`：通过。

## P8 终态门禁修正（2026-08-24）

- [x] 新增终态架构测试，稳定复现并阻止 Shell 按业务 key 下转具体 Feature Controller/Page/Service、Feature 间导入内部实现，以及旧 View 保留 `controls_dict`/`parent._xxx` 完整实现。
- [x] `MainWindow` 改为只通过 `ApplicationRuntime`、`FeatureRuntime` 和通用 `ApplicationShellContribution` 接入功能；Shell contribution 保持 Qt-free，Qt 提示/进度能力通过 `ShellPort` 提供。
- [x] `features/audio/album_map.py` 改用 `app/shared/lua.py` 的通用解析入口，消除 Audio → Characters Parser 的跨 Feature 内部依赖。
- [x] `audio_view.py`、`preview_view.py`、`version_view.py` 经生产代码、测试、工具和正式文档引用归零核对后保持删除；测试已切换到对应 Feature Page。`character_view.py` 仍作为明确登记的兼容入口保留。
- [x] 精确外部 basetemp 全量测试：原有 Sol 复核的 143 项基线加上 3 项终态架构测试后为 146 collected / 146 passed；此前 180 项记录标记为不可复现的旧快照，不再作为当前证据。
- [x] Ruff：`ruff check --no-cache app tests` 通过。
- [x] 不落盘 AST、Feature/Platform import smoke、临时运行目录 MainWindow Qt offscreen smoke 和终态架构复现器均通过：`AST_OK 182`、`MAIN_WINDOW_RUNTIME_SMOKE_OK`、`ARCHITECTURE_TERMINAL_OK`。
- [x] `app`、`tests`、`tools` 的旧 `app.core` 生产引用归零，删除的三个旧 View 无有效引用；`git diff --check` 通过。
- [ ] Sol 独立复核和用户实机验收：待进行；本分支仍未推送、未创建 PR。

## 纯文档净化验证（2026-08-24）

- [x] 正式文档关键词审查：未发现“下一阶段”“后续拆分”“迁移期”“当前迁移顺序”等已完成 P8 后的旧状态描述；保留内容均为当前兼容入口或真实未完成事项。
- [x] `references.md` 当前结论由新鲜 `rg`/AST 证据生成，P0 旧关系已明确归入历史快照；未覆盖交接包中的 `Sol 复核` 区域。
- [x] `python -m ruff check --no-cache app tests`：通过。
- [x] `git diff --check`：通过。
- [x] 本轮只改文档与 `app/bootstrap/app_factory.py` 顶部文档字符串，未改生产逻辑或测试；待建立本地 docs checkpoint。
