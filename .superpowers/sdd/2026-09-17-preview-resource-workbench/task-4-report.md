# Task 4 实施报告：Burst Head 与图集资源组

## 状态

已完成并提交。

- Commit: `3156461c` (`feat: catalog burst head and atlas materials`)
- 工作范围严格限制为 brief 列出的五个代码/测试路径；本报告按 brief 要求单独写入，未加入该 commit。

## 实施内容

- 新增 `app/features/preview/material_catalog.py`：
  - 新增冻结数据类 `GameMaterialRecord`、`AtlasResourceGroup`、`GameMaterialCatalog` 和 `MaterialExportSummary`。
  - `is_burst_head_resource()` 识别 `burst-head`、`burst_head`、`bursthead`，使用路径/名称 token 边界，避免把 `dialoghead`、`head` 或 `burst-head-extra` 仅因子串相似误判。
  - `discover_game_materials()` 递归发现资源，按内容 SHA-256 保存稳定指纹，保留真实来源路径；按包名合并 `*_fui.bank` 与 `*_fui.bytes`，优先使用同包的 `.bytes` 作为切割源，并保留同目录图集图片路径。
  - `export_game_materials()` 将 Burst Head 导出到 `output/game_material/burst-head/`，将每个图集传入独立的 `output/fgui/<package_name>/` 目录；未知资源不进入 Burst Head 导出。
  - Burst Head 文件示例路径为 `output/game_material/burst-head/10080.png`。
  - Burst Head 文件名经过安全清理；目标冲突时使用指纹确定性后缀，并继续保留既有文件。缺源或 splitter 异常会累计失败数和诊断信息，不清理既有输出。
- 修改 `app/features/preview/service.py`：
  - 增加 `discover_game_materials()` 与 `export_game_materials()` 服务入口。
  - 默认注入现有 `UIPackageTool.split_atlas`，同时支持测试/调用方注入 splitter。
- 修改 `app/features/preview/fgui_atlas.py`：
  - 保留旧的根目录调用行为。
  - 当调用方传入已命名的包目录时，切割结果直接落入该目录，从而支持新的 `output/fgui/<package_name>/` 入口；解析算法仍由现有 `UIPackageTool` 执行。
- 新增测试：
  - `test_preview_material_catalog.py` 覆盖 token 边界、包分组、稳定输出目录、失败/缺源诊断、碰撞安全和 service 默认注入。
  - `test_fgui_atlas.py` 覆盖显式包目录下的既有 UIPackageTool 切割行为。

## TDD 证据

1. 先写测试并运行 focused suite，得到预期的 `ModuleNotFoundError: app.features.preview.material_catalog` 红灯。
2. 写入最小 catalog、导出、service 和 FGUI 目录适配实现。
3. focused suite 首次绿灯后，补充 metadata/path 健壮性、忽略错误 kind 和 `.bytes` 优先选择等实现清理，再次验证保持绿灯。

## 验证

- 必需 focused suite：

  `E:\All-Projects\XL\.venv\Scripts\python.exe -m pytest tests/test_preview_material_catalog.py tests/test_fgui_atlas.py tests/test_preview_feature.py -q`

  结果：`11 passed`。
- Ruff：`All checks passed!`
- staged diff check：`git diff --cached --check` 通过。
- 最终 staged scope：仅 brief 指定的五个路径。

## Concern

- 受管 sandbox 对既有 pytest 临时目录/cache 目录和 Git index 有 ACL 限制。普通权限下 pytest 会在创建 `tmp_path` 前触发 `PermissionError`，Git staging 会触发 `.git/index.lock: Permission denied`；已使用项目指定解释器和获批的受控 elevated 执行完成 focused suite、stage、diff 检查和 commit。没有安装依赖，也没有修改无关目录或文件。

## 第三修正轮

提交 `5b801006` 的最终复审后，可靠持久化、冲突诊断与重试行为已在同目录的 `task-4-fix3-report.md` 中补充修正和验证记录。
