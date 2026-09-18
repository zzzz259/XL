# 图片预览输出管线与资源浏览器实施计划

> **执行约束：** 使用 `E:\All-Projects\XL\.venv\Scripts\python.exe`。每个阶段先写失败测试，再实现最小改动、运行聚焦验证、提交独立 Git commit。不得把原始资源从 `data/material` 删除。

**目标：** 将图片资源处理从预览页加载阶段前移到 AS 导入后处理阶段，并在 `output` 同时交付原始 Spine、角色皮肤 PNG、切割图集和 Burst Head；预览页面只消费最终输出。

**范围：** `xl_updata_tool/app/features/preview`、AS 导入后处理工作流、预览 UI、资源文档和相关测试。

## 阶段 1：输出模型与资源发布器

**新增/修改：** `preview/output_publisher.py`、`preview/resource_catalog.py`、`preview/service.py`、相关测试。

- [ ] 先写失败测试：原始 Spine 按 source key 复制到 `output/spine`；`.skel`、atlas 和关联纹理一并保留；来源目录不变。
- [ ] 先写失败测试：发布器可重复执行，不产生错误覆盖；无法识别角色的资源进入 `unmatched`。
- [ ] 实现原始 Spine 发布和 `preview_index.json` 基础索引。
- [ ] 运行聚焦测试。
- [ ] 提交：`feat: publish raw spine resources to output`。

## 阶段 2：预处理 Worker 接入 AS 导入工作流

**新增/修改：** `preview/workers/preview_postprocess.py`、`importer/service.py`、`bootstrap/workflows.py`、注册和测试。

- [ ] 先写失败测试：导入结果把图片预处理列为待处理项。
- [ ] 先写失败测试：音频、图片、角色后处理按约定顺序执行，进度共享，任务完成前不关闭进度窗口。
- [ ] 先写失败测试：取消、失败和重复启动行为可观测，已完成输出保留。
- [ ] 实现图片预处理 Worker：发布 Spine、查询皮肤、切割 FGUI、处理 Burst Head、写入索引。
- [ ] 接入 `PostProcessorRegistry` 和 `ImportPostprocessWorkflow`。
- [ ] 运行 importer/workflow 聚焦测试并提交：`feat: run preview processing after import`。

## 阶段 3：FGUI/Burst Head 输出与输出目录目录模型

**修改：** `preview/material_catalog.py`、`preview/fgui_atlas.py`、`preview/catalog.py`、输出目录模型及测试。

- [ ] 先写失败测试：素材目录只读取 `output/fgui` 和 `output/game_material/burst-head` 的最终文件。
- [ ] 先写失败测试：每个 FGUI 包独立目录，记录切割信息；原始 atlas 不作为展示条目。
- [ ] 实现从 output 构建 `GameMaterialOutputCatalog`，兼容旧输出但不回退到原始素材展示。
- [ ] 运行聚焦测试并提交：`feat: index processed game materials`。

## 阶段 4：静态 Spine 导出与角色/皮肤输出

**修改：** `preview/export_plan.py`、`preview/spine_adapter.py`、`preview/dialogs/export_settings.py`、导出 Worker 及测试。

- [ ] 先写失败测试：默认设置为静态图；静态命令不包含 `-a idle`，动画模式才包含动画参数。
- [ ] 先写失败测试：导出结果写入 `output/character/<character>/<skin>/` 并生成 `metadata.json`。
- [ ] 验证并实现 SpineViewerCLI 的静态导出参数，避免依赖错误的动画名。
- [ ] 实现导出期间的当前文件名、进度和取消语义。
- [ ] 运行导出聚焦测试并提交：`feat: support static spine skin export`。

## 阶段 5：预览页大图标浏览与 Spine 名称布局

**修改：** `preview/page.py`、`preview/controller.py`、`preview/spine_tree.py`、图标浏览组件及测试。

- [ ] 先写失败 Qt 测试：进入预览页不调用重型发现/切割；只读取输出索引。
- [ ] 先写失败 Qt 测试：角色页按角色/皮肤目录显示 Windows 大图标；游戏素材页按 Burst Head/图集包目录显示已切割图片。
- [ ] 先写失败 Qt 测试：Spine 名称列足够宽且 tooltip 保留全名。
- [ ] 实现文件夹卡片、面包屑/返回、空状态和加载状态。
- [ ] 调整树列宽和状态展示，运行 Qt 聚焦测试并提交：`feat: browse processed preview outputs`。

## 阶段 6：统一新状态和跨页面刷新

**修改：** `preview/resource_state.py`、`preview/item.py`、`preview/controller.py`、缩略图/图标模型及测试。

- [ ] 先写失败测试：Spine、角色文件夹、皮肤文件夹、FGUI 和 Burst Head 共用稳定资源键。
- [ ] 先写失败测试：“全部标为已读”后当前页面和父目录立即刷新，不需要重启。
- [ ] 先写失败测试：新资源在不同分页的显示一致，不残留白色“新”。
- [ ] 实现统一状态传播和 UI 刷新信号。
- [ ] 运行状态聚焦测试并提交：`fix: synchronize preview unread state`。

## 阶段 7：兼容、文档和全量验证

**修改：** `docs/开发文档指南.md`、`docs/版本历史.md`、验收测试。

- [ ] 先写验收测试：旧 `output/character/*.png`、旧 FGUI 输出仍能读取；新流程不自动拼接立绘。
- [ ] 更新输出目录、处理时机、静态导出、素材预览和新状态说明。
- [ ] 使用项目 Python 运行：
  - `E:\All-Projects\XL\.venv\Scripts\python.exe -m pytest -q`
  - `E:\All-Projects\XL\.venv\Scripts\python.exe -m ruff check app tests`
  - `E:\All-Projects\XL\.venv\Scripts\python.exe -m compileall -q app`
  - `git diff --check`
- [ ] 检查 `git status` 和输出目录，确认没有临时测试目录或杂项文件。
- [ ] 提交：`docs: document preview output pipeline`。

## 提交顺序

每个阶段独立提交，提交后立即运行该阶段验证。全部阶段通过后再进行一次完整测试和人工检查；最后再按用户要求整理为推送/PR。
