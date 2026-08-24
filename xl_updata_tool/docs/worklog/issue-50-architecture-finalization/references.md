# Issue #50 引用与迁移基线

## 目的

本清单用于 P1-P8 的引用归零判定。它记录的是实施分支当前 tracked 源码、测试、工具和正式文档中的真实入口，不把 `.pyc`、缓存和运行时日志计入有效引用。每次删除迁移期文件前，必须重新执行 `git grep` 与必要的 AST 检查，并在本文件和 `verification.md` 留下新鲜证据。

## 当前仍受保护的公开入口

- `xl_updata_tool/main.py`：正式启动入口。
- `xl_updata_tool/app/ui/legacy/asset_browser_entry.py`：登记的兼容入口，未完成替代入口与文档迁移前不得删除。
- `xl_updata_tool/app/ui/` 下现有页面、对话框、适配器和组件：仍被 Feature、Shell 或测试使用，不能按目录整体清理。
- `xl_updata_tool/app/core/` 下被 Feature、platform、工具、测试或公开文档引用的模块：必须逐模块迁移和证明归零。

## 当前旧层文件清单

### `app/ui/features`

`audio_controller.py`、`export_controller.py`、`fgui_atlas.py`、`preview_controller.py`。

### `app/ui/views`

`character_view.py`。`audio_view.py`、`preview_view.py`、`version_view.py` 已在生产代码、测试、工具和正式文档引用归零后删除；不得为了兼容旧路径重新恢复。

### `app/ui/workers`

`audio_decrypt.py`、`batch_export.py`、`composite_export.py`、`download.py`、`image_loader.py`、`import_as.py`、`lua_decrypt.py`、`preview_export.py`。

### `app/ui/adapters`、`widgets`、`dialogs`

- `adapters/spine_adapter.py`：Preview 适配器仍被 Feature 使用。
- `widgets/character_profile.py`、`widgets/drag_list.py`、`widgets/view_chrome.py`：其中部分仍被 Feature 或 Shared UI 使用。
- `dialogs/character_select.py`、`dialogs/export_settings.py`、`dialogs/image_viewer.py`：仍是 UI 公共组件，不能按“旧层”名称直接删除。

## 当前直接引用关系

- Feature Worker → 旧 Worker：
  - `features/audio/worker.py` 已不再引用旧音频 Worker；Qt-free 处理位于 `features/audio/processing.py`
  - `features/importer/worker.py` 已不再引用旧导入 Worker；Qt-free 处理位于 `features/importer/processing.py`
  - `features/preview/worker.py` → `app.ui.workers.image_loader`、`preview_export`、`batch_export`、`composite_export`
  - `features/versions/worker.py` → `app.ui.workers.download`
- Feature Page/Adapter → 旧 View/UI：
  - `features/preview/adapter.py` → `app.ui.adapters.spine_adapter`
  - `features/preview/fgui.py` → `app.ui.features.fgui_atlas`
  - `features/characters/page.py` → `app.ui.widgets.character_profile`
  - `shared/qt/chrome.py` → `app.ui.widgets.view_chrome`
- Shell → `FeatureRuntime`、`ApplicationShellContribution` 和通用 `ShellPort`；Shell 不按业务 key 获取具体 Feature Controller/Page/Service。
- Platform → 旧 `app.core` 基础设施门面；这些是 P6 的收口对象，不属于本阶段删除范围。
- 工具/测试仍直接使用多个 `app.core.*` 与 `app.ui.workers.*` 入口；音频旧入口目前是薄兼容门面，其他领域必须在迁移和兼容策略明确后再处理。

## P4 音频 Worker 迁移后事实

- `app/features/audio/processing.py` 是 Qt-free 音频处理器，承载 bytes/bank 筛选、debank 调用、分类映射、增量路径索引、语音命名修正、旧产物清理、BGM 审计和取消检查。
- `app/features/audio/worker.py` 只承载 QThread 生命周期、取消请求和处理器回调到既有信号的映射；生产 Controller 只引用该 Feature Worker。
- `app/ui/workers/audio_decrypt.py` 保留为兼容入口，仅转发 `AudioDecryptProcessor` 和 `AudioDecryptWorker`，没有第二份实现。
- P4 暂不删除旧入口：它仍属于登记过的外部/测试兼容面；删除前必须重新证明 tracked 代码、测试、工具和正式文档引用归零。

## P5 Importer Worker 迁移后事实

- `app/features/importer/processing.py` 是 Qt-free AssetStudio 导入处理器，承载 Bundle 修复、资源映射、分类导出、staging 提交/回滚、Lua 发布、精准 Bundle 输入和取消检查。
- `app/features/importer/worker.py` 只承载 QThread 生命周期、取消请求和既有进度/阶段/分类/完成信号映射；ImporterController 的结果组装契约未改变。
- `app/ui/workers/import_as.py` 保留为 `ImportASWorker` 旧类名兼容门面，没有第二份导入实现。
- P5 暂不删除旧入口：它仍属于登记过的外部/测试兼容面；删除前必须重新证明 tracked 代码、测试、工具和正式文档引用归零。

## 当前 `controls_dict` / `parent._` 事实

- `app/ui/views/character_view.py` 是仍登记的兼容工厂；Audio、Preview、Versions 已使用各自 Feature Page。
- Feature Page 自持控件，不通过旧 View 的控件字典建页内引用。
- `features/preview/export_controller.py` 仍读取 `parent._...` worker、状态和骨架映射；P1 必须先建立行为等价的 Feature-owned 状态接口，再删除兼容桥。
- 当前 P0 不修改上述生产实现，只把这些事实作为后续阶段的删除前置条件。

## P0 基线命令与结果（历史快照）

- `pytest`：127 collected，127 passed。
- `ruff check --no-cache app tests`：`All checks passed!`。
- AST 解析：149 个 Python 文件通过。
- 根入口、bootstrap 和全部 Feature import smoke：`IMPORT_SMOKE_OK`。
- Qt offscreen smoke：`QT_SMOKE_OK`，默认版本页可见，预览/音频/角色页默认隐藏。
- 工作区存在既有 `.pytest_cache` 权限警告；不修改缓存权限、不把它作为源码引用。

## 重新核对规则

删除或改名前，至少执行：

```text
git grep -n "被迁移模块或符号" -- xl_updata_tool
python -B -m compileall -q xl_updata_tool/app xl_updata_tool/tests
pytest -q
ruff check --no-cache xl_updata_tool/app xl_updata_tool/tests
```

若仍有正式调用方、测试入口、工具入口或登记的兼容入口，保留薄门面并把调用方写入本清单；不得为目录整洁强删。

## P8 终态修正后的当前引用结论（2026-08-24）

- `app/ui/views/audio_view.py`、`preview_view.py`、`version_view.py` 的 tracked 源码、生产代码、测试、工具和正式文档引用均已归零，因此保持删除。
- `app/ui/views/character_view.py` 仍是明确登记的兼容入口；其调用方继续保留在本清单，不将其与已归零的三个旧 View 混同。
- `features/audio/album_map.py` 通过 `app.shared.lua` 使用通用 Lua 文本解析，不再跨 Feature 导入 Characters Parser 内部实现。
- `app/ui/main_window.py` 只通过 Runtime 和通用 Shell contribution 接入功能，不按业务 key 下转具体 Controller/Page/Service。
