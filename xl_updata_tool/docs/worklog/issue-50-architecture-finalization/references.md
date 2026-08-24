# Issue #50 引用与迁移记录

本文分为两部分：P8 当前引用结论和 P0 历史快照。当前结论只依据实施分支 tracked 源码、测试、工具和正式文档的最新 `rg`/AST 核对；历史快照只用于解释迁移背景，不作为当前架构判断依据。

## P8 当前引用结论（2026-08-24）

### 当前稳定边界

- `app/bootstrap/` 负责 Composition Root、Feature 注册和 Shell contribution。
- `app/platform/` 负责路径、文件、数据库、Bundle、进程、下载、诊断和 Lua 成品仓库。
- `app/shared/` 负责跨 Feature 的无 Qt 契约与通用 Lua 文本解析。
- `app/features/{audio,characters,importer,preview,versions}/` 分别拥有对应领域的页面、控制器、服务、Worker 和纯逻辑。
- `app/ui/main_window.py` 只消费 Runtime、Page 和通用 Shell contribution；不按业务 key 获取具体 Feature Controller/Page/Service。
- `app/core/` 已无 Python 模块，生产代码不再依赖该路径。

### 新鲜引用与终态门禁证据

- `rg -n -g '*.py' "app\.core|app\.ui\.views|app\.ui\.workers|app\.ui\.features|app\.ui\.adapters|app\.ui\.dialogs|app\.ui\.widgets|parent\._|controls_dict" app tests tools`：生产代码未发现 `app.core`、已删除三个旧 View 或旧 Worker 的实际导入；剩余命中属于测试中的兼容入口和架构禁止性断言。
- `rg -n -g '*.py' "from app\.features\.|import app\.features\." app/features`：Feature 内部导入限定在同一 Feature；跨功能的通用 Lua 文本解析通过 `app.shared.lua` 提供。
- `python E:\AI-Agent\Codex-GPT\scripts\xl_sol_architecture_acceptance.py`：`AST_OK 182`、`MAIN_WINDOW_RUNTIME_SMOKE_OK`、`ARCHITECTURE_TERMINAL_OK`。
- `audio_view.py`、`preview_view.py`、`version_view.py` 的生产代码、测试、工具和正式文档引用均已归零，因此保持删除；不得为了复现旧路径而恢复。

### 当前保留的兼容入口及真实调用方

| 入口 | 当前实现 | tracked 调用方/用途 |
|---|---|---|
| `app/ui/views/character_view.py` | 转发到 `CharacterPage` | `tests/test_characters_feature.py` 的兼容行为测试 |
| `app/ui/features/audio_controller.py` | 音频树兼容辅助入口 | `tests/test_audio_feature.py`、`tests/test_ui_chrome.py` 及架构入口测试 |
| `app/ui/features/preview_controller.py` | 预览条目兼容辅助入口 | `tests/test_architecture.py` 的入口测试 |
| `app/ui/features/export_controller.py` | 转发到 `app/features/preview/export_controller.py` | `tests/test_preview_feature.py`、`tests/test_architecture.py` |
| `app/ui/widgets/character_profile.py` | 角色资料控件兼容入口 | `tests/test_character_profile.py` |
| `app/ui/dialogs/{character_select,export_settings,image_viewer}.py` | 兼容对话框入口 | `tests/test_ui_dialogs.py` |
| `app/ui/legacy/asset_browser_entry.py` | 明确登记的历史资源浏览器入口 | 作为公开兼容入口保留，不属于主流程 |

`app/ui/workers/{audio_decrypt,import_as}.py`、`app/ui/adapters/spine_adapter.py` 等文件仍存在，但当前 `app`、`tests`、`tools` 未发现实际导入；它们不在本次纯文档净化中删除，后续若要处理必须单独完成调用方、外部兼容面和行为验证核对。

### 当前删除结论

- `app/core/` 下纯转发 Python 文件已在引用归零后删除。
- `app/ui/views/audio_view.py`、`preview_view.py`、`version_view.py` 已在引用归零后删除。
- `character_view.py`、Audio/Preview 兼容辅助入口和兼容对话框仍有测试调用方，不能按目录名称直接删除。
- 当前文档不得把 P0 的旧关系描述为现状，也不得把已归零的旧路径登记为当前调用方。

## P0 历史快照（仅供追溯）

以下内容记录 P0 建基线时的迁移态，不代表 P8 当前代码：

- 当时 Feature Worker、Preview Page、Versions Page 和 Shell 仍直接依赖部分 `app.ui` 旧入口。
- 当时 Platform 与多个 Feature 仍通过 `app.core` 兼容层访问基础设施或领域实现。
- 当时 Preview 导出仍存在 `controls_dict`、`parent._...` 和旧 View 控件桥接。
- 当时 `audio_view.py`、`preview_view.py`、`version_view.py` 尚未完成调用方迁移，不能按引用归零规则删除。
- P0 基线验证为 pytest 127 项、AST 149 个 Python 文件、Ruff、import smoke 和 Qt offscreen smoke 通过；这些数字是历史基线，不覆盖 P8 当前验证结果。

## 重新核对规则

删除或改名迁移入口前，必须重新执行：

```text
git grep -n "被迁移模块或符号" -- xl_updata_tool
python E:\AI-Agent\Codex-GPT\scripts\xl_sol_architecture_acceptance.py
python -m pytest -q -p no:cacheprovider --basetemp <外部临时目录>
python -m ruff check --no-cache app tests
git diff --check
```

若仍有正式调用方、测试入口、工具入口或明确登记的外部兼容面，保留薄门面并把调用方写入本清单；不得为目录整洁强删，也不得为了旧脚本恢复已归零路径。
