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

`audio_view.py`、`character_view.py`、`preview_view.py`、`version_view.py`。

### `app/ui/workers`

`audio_decrypt.py`、`batch_export.py`、`composite_export.py`、`download.py`、`image_loader.py`、`import_as.py`、`lua_decrypt.py`、`preview_export.py`。

### `app/ui/adapters`、`widgets`、`dialogs`

- `adapters/spine_adapter.py`：Preview 适配器仍被 Feature 使用。
- `widgets/character_profile.py`、`widgets/drag_list.py`、`widgets/view_chrome.py`：其中部分仍被 Feature 或 Shared UI 使用。
- `dialogs/character_select.py`、`dialogs/export_settings.py`、`dialogs/image_viewer.py`：仍是 UI 公共组件，不能按“旧层”名称直接删除。

## 当前直接引用关系

- Feature Worker → 旧 Worker：
  - `features/audio/worker.py` → `app.ui.workers.audio_decrypt`
  - `features/importer/worker.py` → `app.ui.workers.import_as`
  - `features/preview/worker.py` → `app.ui.workers.image_loader`、`preview_export`、`batch_export`、`composite_export`
  - `features/versions/worker.py` → `app.ui.workers.download`
- Feature Page/Adapter → 旧 View/UI：
  - `features/preview/page.py` → `app.ui.views.preview_view`
  - `features/preview/adapter.py` → `app.ui.adapters.spine_adapter`
  - `features/preview/fgui.py` → `app.ui.features.fgui_atlas`
  - `features/versions/page.py` → `app.ui.views.version_view`
  - `features/characters/page.py` → `app.ui.widgets.character_profile`
  - `shared/qt/chrome.py` → `app.ui.widgets.view_chrome`
- Shell → 具体 Feature Page/Controller/Service 以及多个 `app.core` 领域入口；这正是 P2 的 Composition Root/ShellPort 迁移目标，当前不能伪装成已完成。
- Platform → 旧 `app.core` 基础设施门面；这些是 P6 的收口对象，不属于本阶段删除范围。
- 工具/测试仍直接使用多个 `app.core.*` 与 `app.ui.workers.*` 入口，必须在迁移和兼容策略明确后再处理。

## 当前 `controls_dict` / `parent._` 事实

- `app/ui/views/audio_view.py`、`preview_view.py`、`character_view.py` 仍是迁移期控件字典/父窗口回调入口。
- `features/preview/page.py` 仍通过旧 View 的控件字典建页内引用。
- `features/preview/export_controller.py` 仍读取 `parent._...` worker、状态和骨架映射；P1 必须先建立行为等价的 Feature-owned 状态接口，再删除兼容桥。
- 当前 P0 不修改上述生产实现，只把这些事实作为后续阶段的删除前置条件。

## P0 基线命令与结果

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
