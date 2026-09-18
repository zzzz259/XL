# 图片预览资源工作台 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 将图片预览升级为按角色/皮肤身份管理的三分页资源工作台，并支持用户选择皮肤、配置导出、图集切割和 Burst Head 素材浏览。

**Architecture:** 在 `app/features/preview` 内新增 Qt 无关的资源模型、索引、状态和导出计划；页面只负责控件，控制器负责生命周期，Worker 负责异步 CLI/图像任务。Spine 内部皮肤元数据与资源路径/Prefab 元数据负责身份匹配，文件名只保留兼容和展示用途。

**Tech Stack:** Python 3、PySide6、Pillow、pytest、SpineViewerCLI、现有 `UIPackageTool` 和项目 `.venv`。

**Spec:** `xl_updata_tool/docs/superpowers/specs/2026-09-17-preview-resource-workbench-design.md`

## Execution status (2026-09-18)

- Task 1 complete: `ce774862..f4955bbf`
- Task 2 complete: `2c2a2df7..1e3410e6`
- Task 3 complete: `7d9dc7b3..80d0768a`
- Task 4 complete: `3156461c..2e5574b0`
- Task 5 complete: `0d31d4d2..81c753af`
- Task 6 complete: `f374f93d..43cb501b` (the delegated worker stopped at the usage limit; implementation and verification were completed locally)
- Task 7 complete: `27fd9c71` (same quota constraint; implementation and verification were completed locally)
- Task 8 documentation and final verification complete locally.

Task 6 additionally modified `page.py` to expose the selected-Spine export button. Task 7 uses `thumbnail_model.py` plus page/controller pagination; the legacy image loader remains unchanged because it already provides recursive asynchronous loading.

## Global Constraints

- 必须使用 `E:/All-Projects/XL/.venv/Scripts/python.exe` 运行测试和 Python 工具。
- 不改变 `output/fgui/<包名>/` 既有输出入口。
- 既有 `output/character/<角色ID>/*.png` 必须可读，并作为 legacy 未归类资源展示。
- 皮肤主键不得由不规范文件名构成，必须使用 Spine 内部皮肤信息和附件集合指纹。
- 无法可靠确认角色 ID 的资源进入“未匹配资源”，不得猜测归类。
- 导出阶段不得自动执行背景合成或最终立绘拼接。
- 导出、加载和切割任务必须避免重复启动，并提供取消或明确不可取消状态。
- 新资源状态必须递归传播到角色、皮肤、图集和游戏素材父节点，并立即刷新 UI。

---

### Task 1: 资源模型、身份键和新状态仓库

**Files:**
- Create: `xl_updata_tool/app/features/preview/resource_model.py`
- Create: `xl_updata_tool/app/features/preview/resource_state.py`
- Modify: `xl_updata_tool/app/features/preview/service.py`
- Test: `xl_updata_tool/tests/test_preview_resource_model.py`
- Test: `xl_updata_tool/tests/test_preview_resource_state.py`

**Interfaces:**
- `SpineSkinRecord(character_id, source_skel, atlas_path, skin_name, attachment_fingerprint, display_name, status)`。
- `PreviewResourceCatalog(characters, skins, atlases, burst_heads, unmatched)`。
- `skin_key(record) -> str`。
- `PreviewResourceState(path).is_new(fingerprint)`, `.mark_read(fingerprint)`, `.save()`。

- [ ] **Step 1: Write the failing tests**：覆盖相同显示名但附件集合不同会产生不同皮肤键；缺失角色 ID 会进入 `unmatched`；父节点在任意子项未读时为新。
- [ ] **Step 2: Run the focused tests**：`E:\All-Projects\XL\.venv\Scripts\python.exe -m pytest tests/test_preview_resource_model.py tests/test_preview_resource_state.py -q`，确认因模块/接口不存在而失败。
- [ ] **Step 3: Implement the minimal immutable records and JSON state store**：指纹和键只使用规范化路径、角色 ID、skin 名和附件指纹；状态文件采用原子替换写入。
- [ ] **Step 4: Run focused tests again**，确认全部通过。
- [ ] **Step 5: Commit**：`git add xl_updata_tool/app/features/preview/resource_model.py xl_updata_tool/app/features/preview/resource_state.py xl_updata_tool/app/features/preview/service.py xl_updata_tool/tests/test_preview_resource_model.py xl_updata_tool/tests/test_preview_resource_state.py && git commit -m "feat: add preview resource identity model"`。

### Task 2: Spine/皮肤发现与可靠匹配

**Files:**
- Create: `xl_updata_tool/app/features/preview/resource_catalog.py`
- Modify: `xl_updata_tool/app/features/preview/catalog.py`
- Modify: `xl_updata_tool/app/features/preview/spine_adapter.py`
- Test: `xl_updata_tool/tests/test_preview_resource_catalog.py`
- Test: `xl_updata_tool/tests/test_spine_adapter.py`

**Interfaces:**
- `SpineQueryRunner.query_skins(skel_path, atlas_path) -> SkinQueryResult`。
- `discover_preview_resources(material_dir, character_data=None, query_runner=None) -> PreviewResourceCatalog`。
- `parse_skin_query_output(stdout) -> tuple[str, ...]`。
- `resolve_character_id(path, metadata) -> str | None`。

- [ ] **Step 1: Write failing tests**：模拟 `query --skin` 输出多个 skin；模拟 skin 名重复但附件指纹不同；验证缺少 atlas、CLI 失败和不能确认角色 ID 的结果。
- [ ] **Step 2: Run focused tests**：`E:\All-Projects\XL\.venv\Scripts\python.exe -m pytest tests/test_preview_resource_catalog.py tests/test_spine_adapter.py -q`，确认测试先失败。
- [ ] **Step 3: Implement injectable CLI query and catalog discovery**：使用现有 ToolLocator 定位 CLI；不再调用 `extract_motion_names()` 作为皮肤来源；保留 stderr 摘要和资源状态。
- [ ] **Step 4: Run focused tests and existing preview catalog tests**：确认新增测试及 `tests/test_preview_catalog.py` 通过。
- [ ] **Step 5: Commit**：`git add xl_updata_tool/app/features/preview/resource_catalog.py xl_updata_tool/app/features/preview/catalog.py xl_updata_tool/app/features/preview/spine_adapter.py xl_updata_tool/tests/test_preview_resource_catalog.py xl_updata_tool/tests/test_spine_adapter.py && git commit -m "feat: discover spine skins by metadata"`。

### Task 3: 导出计划、皮肤目录和 CLI 参数配置

**Files:**
- Create: `xl_updata_tool/app/features/preview/export_plan.py`
- Modify: `xl_updata_tool/app/features/preview/workers/preview_export.py`
- Modify: `xl_updata_tool/app/features/preview/spine_adapter.py`
- Test: `xl_updata_tool/tests/test_preview_export_plan.py`
- Test: `xl_updata_tool/tests/test_preview_export_worker.py`

**Interfaces:**
- `ExportSettings` 数据类：`animation`, `scale`, `max_resolution`, `margin`, `transparent`, `pma`, `format`, `fps`。
- `build_export_plan(records, settings, output_dir) -> tuple[SkinExportJob, ...]`。
- `build_spine_export_command(job, spine_cli) -> list[str]`。
- `PreviewExportWorker(jobs, settings, runner)`：发出 `progress(current,total,label)`、`finished(summary)`、`error(message)`。

- [ ] **Step 1: Write failing tests**：验证一个 job 只对应一个角色皮肤目录；命令包含内部 skin 名而不是 PNG 文件名；配置可生成透明 PNG；计划不包含自动 composite job。
- [ ] **Step 2: Run focused tests**：`E:\All-Projects\XL\.venv\Scripts\python.exe -m pytest tests/test_preview_export_plan.py tests/test_preview_export_worker.py -q`，确认红灯。
- [ ] **Step 3: Implement plan builder and worker**：输出到 `output/character/<character_id>/<skin_key>/`；写入 `metadata.json`；取消时停止后续 job，不删除其他分类输出。
- [ ] **Step 4: Run focused tests**，确认绿灯并覆盖重复启动保护。
- [ ] **Step 5: Commit**：`git add xl_updata_tool/app/features/preview/export_plan.py xl_updata_tool/app/features/preview/workers/preview_export.py xl_updata_tool/app/features/preview/spine_adapter.py xl_updata_tool/tests/test_preview_export_plan.py xl_updata_tool/tests/test_preview_export_worker.py && git commit -m "feat: export selected spine skins"`。

### Task 4: Burst Head 与图集资源组

**Files:**
- Create: `xl_updata_tool/app/features/preview/material_catalog.py`
- Modify: `xl_updata_tool/app/features/preview/fgui_atlas.py`
- Modify: `xl_updata_tool/app/features/preview/service.py`
- Test: `xl_updata_tool/tests/test_preview_material_catalog.py`
- Test: `xl_updata_tool/tests/test_fgui_atlas.py`

**Interfaces:**
- `discover_game_materials(material_dir) -> GameMaterialCatalog`。
- `is_burst_head_resource(path, metadata) -> bool`。
- `AtlasResourceGroup(package_name, source_path, sprites)`。
- `export_game_materials(catalog, output_dir, splitter) -> MaterialExportSummary`。

- [ ] **Step 1: Write failing tests**：覆盖 `burst-head`、`burst_head`、`bursthead` 变体；验证普通 dialoghead 不会仅因名字相似被归类；验证每个图集组有独立输出目录。
- [ ] **Step 2: Run focused tests**：`E:\All-Projects\XL\.venv\Scripts\python.exe -m pytest tests/test_preview_material_catalog.py tests/test_fgui_atlas.py -q`，确认红灯。
- [ ] **Step 3: Implement catalog and reuse `UIPackageTool`**：FGUI 仍写 `output/fgui/<包名>/`；Burst Head 写 `output/game_material/burst-head/`；保留来源元数据。
- [ ] **Step 4: Run focused tests**，确认绿灯。
- [ ] **Step 5: Commit**：`git add xl_updata_tool/app/features/preview/material_catalog.py xl_updata_tool/app/features/preview/fgui_atlas.py xl_updata_tool/app/features/preview/service.py xl_updata_tool/tests/test_preview_material_catalog.py xl_updata_tool/tests/test_fgui_atlas.py && git commit -m "feat: catalog burst head and atlas materials"`。

### Task 5: Preview 三分页和 Spine 树

**Files:**
- Modify: `xl_updata_tool/app/features/preview/page.py`
- Create: `xl_updata_tool/app/features/preview/spine_tree.py`
- Modify: `xl_updata_tool/app/features/preview/item.py`
- Test: `xl_updata_tool/tests/test_preview_page.py`
- Test: `xl_updata_tool/tests/test_preview_spine_tree.py`

**Interfaces:**
- `PreviewPage.tabs` 提供 `spine_tab`, `character_tab`, `material_tab`。
- `PreviewSpineTree.set_catalog(catalog)`、`.selected_records()`、`.mark_selected_read()`。
- `PreviewPage.export_requested = Signal(object)`，参数为选中的 `SpineSkinRecord` 集合。

- [ ] **Step 1: Write failing Qt tests**：验证三个分页存在；Spine 页角色/皮肤节点带复选框和新状态；父节点状态随子节点刷新；角色立绘页继续提供缩略图容器。
- [ ] **Step 2: Run focused Qt tests**：`$env:QT_QPA_PLATFORM='offscreen'; E:\All-Projects\XL\.venv\Scripts\python.exe -m pytest tests/test_preview_page.py tests/test_preview_spine_tree.py -q`，确认红灯。
- [ ] **Step 3: Implement page and tree widgets**：复用共享 chrome/theme token；Spine 页不加载缩略图；选中或勾选时发语义信号并同步新状态。
- [ ] **Step 4: Run focused Qt tests**，确认绿灯。
- [ ] **Step 5: Commit**：`git add xl_updata_tool/app/features/preview/page.py xl_updata_tool/app/features/preview/spine_tree.py xl_updata_tool/app/features/preview/item.py xl_updata_tool/tests/test_preview_page.py xl_updata_tool/tests/test_preview_spine_tree.py && git commit -m "feat: add preview resource tabs"`。

### Task 6: 导出配置窗口与控制器生命周期

**Files:**
- Modify: `xl_updata_tool/app/features/preview/dialogs/export_settings.py`
- Modify: `xl_updata_tool/app/features/preview/controller.py`
- Modify: `xl_updata_tool/app/features/preview/factory.py`
- Test: `xl_updata_tool/tests/test_preview_export_settings.py`
- Test: `xl_updata_tool/tests/test_preview_controller.py`

**Interfaces:**
- `ExportSettingsDialog.settings() -> ExportSettings`。
- `PreviewController.discover_resources()`、`.start_selected_export(records)`、`.cancel_export()`。
- `PreviewController.status_changed` 持续提供“发现中/导出中/已取消/完成/失败”文本。

- [ ] **Step 1: Write failing tests**：验证控件能构造合法 `ExportSettings`；重复点击不会创建第二个 Worker；取消会清理引用并刷新底部状态；导出完成不自动调用 composite。
- [ ] **Step 2: Run focused tests**：`E:\All-Projects\XL\.venv\Scripts\python.exe -m pytest tests/test_preview_export_settings.py tests/test_preview_controller.py -q`，确认红灯。
- [ ] **Step 3: Implement dialog/controller wiring**：用 QComboBox、QSpinBox、QCheckBox；导出期间禁用开始按钮并显示当前文件名和 x/N；刷新后重新加载三分页数据。
- [ ] **Step 4: Run focused tests**，确认绿灯。
- [ ] **Step 5: Commit**：`git add xl_updata_tool/app/features/preview/dialogs/export_settings.py xl_updata_tool/app/features/preview/controller.py xl_updata_tool/app/features/preview/factory.py xl_updata_tool/tests/test_preview_export_settings.py xl_updata_tool/tests/test_preview_controller.py && git commit -m "feat: configure preview exports from UI"`。

### Task 7: 缩略图分页、素材文件夹和状态同步

**Files:**
- Modify: `xl_updata_tool/app/features/preview/workers/image_loader.py`
- Modify: `xl_updata_tool/app/features/preview/item.py`
- Modify: `xl_updata_tool/app/features/preview/controller.py`
- Test: `xl_updata_tool/tests/test_preview_thumbnail_groups.py`

- [ ] **Step 1: Write failing tests**：验证角色/皮肤分页计数、`burst-head` 和图集目录分组、legacy PNG 可显示且标记为未归类、过滤和选择后底部计数即时更新。
- [ ] **Step 2: Run focused test**：`E:\All-Projects\XL\.venv\Scripts\python.exe -m pytest tests/test_preview_thumbnail_groups.py -q`，确认红灯。
- [ ] **Step 3: Implement grouped recursive loading**：不阻塞 UI；空状态、加载状态和失败状态使用内容区覆盖层；新状态使用资源键而不是 PNG 文件名。
- [ ] **Step 4: Run focused test and existing preview tests**，确认绿灯。
- [ ] **Step 5: Commit**：`git add xl_updata_tool/app/features/preview/workers/image_loader.py xl_updata_tool/app/features/preview/item.py xl_updata_tool/app/features/preview/controller.py xl_updata_tool/tests/test_preview_thumbnail_groups.py && git commit -m "feat: group preview thumbnails by resource"`。

### Task 8: 迁移兼容、文档和全量验证

**Files:**
- Modify: `xl_updata_tool/docs/开发文档指南.md`
- Modify: `xl_updata_tool/docs/版本历史.md`
- Modify: `xl_updata_tool/tests/test_preview_feature.py`
- Modify: `xl_updata_tool/tests/test_ownership_acceptance.py`

- [ ] **Step 1: Write failing acceptance tests**：验证旧 `app.ui` Preview 入口仍转发到 Feature；旧 `output/character` 和 `output/fgui` 能被新 Catalog 读取；新导出不自动 composite。
- [ ] **Step 2: Run acceptance tests**：`E:\All-Projects\XL\.venv\Scripts\python.exe -m pytest tests/test_preview_feature.py tests/test_ownership_acceptance.py -q`，确认红灯。
- [ ] **Step 3: Implement compatibility and documentation**：更新目录结构、参数配置、匹配规则、Burst Head 识别限制和拼接操作说明。
- [ ] **Step 4: Run full verification**：
  - `E:\All-Projects\XL\.venv\Scripts\python.exe -m pytest -q`
  - `E:\All-Projects\XL\.venv\Scripts\python.exe -m ruff check app tests`
  - `E:\All-Projects\XL\.venv\Scripts\python.exe -m compileall -q app`
  - `git diff --check`
- [ ] **Step 5: Commit**：`git add xl_updata_tool/docs/开发文档指南.md xl_updata_tool/docs/版本历史.md xl_updata_tool/tests/test_preview_feature.py xl_updata_tool/tests/test_ownership_acceptance.py && git commit -m "docs: document preview resource workbench"`。
