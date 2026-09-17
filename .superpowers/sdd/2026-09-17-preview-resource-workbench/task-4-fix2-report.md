# Task 4 第二修正轮报告：Burst Head 与图集资源组

## 状态

已完成并提交，基于复审基线 `b352aba2`。

- Commit：`HEAD`（本报告与实现同属本轮唯一修正提交）
- 本轮只修改 Task 4 相关实现、测试和报告。

## 根因与修正

1. **metadata 顶层键碰撞**

   `_metadata_entry()` 不再把顶层 mapping 的键当作资源路径候选。路径变体只在 `resources`、`by_path`、`assets`、`items` 等明确资源容器中匹配，或匹配列表项的明确路径字段；因此 `metadata={"type": "burst-head"}` 不会把普通文件（包括文件名为 `type`、`kind`、`name`、`path` 等文件）全局分类。测试同时保留明确资源条目的正例。

2. **Burst Head 导出幂等与 manifest 保存失败**

   每个复制出的文件先以原子替换写入 `<输出文件>.burst-head.json` sidecar，记录规范化来源路径、fingerprint 和目标文件名，再参与 manifest 保存。manifest 最终保存失败时会返回诊断；重试会从 sidecar 找回同一来源和 fingerprint 的目标，不会生成副本。不同来源仍按安全文件名、fingerprint 和来源路径后缀分配不同目标，不覆盖已有文件。

3. **损坏 manifest 诊断化**

   manifest 读取现在校验 UTF-8、JSON、版本、entries 容器、fingerprint 和 output 结构。`UnicodeDecodeError`、`JSONDecodeError` 及结构错误均转为 `MaterialExportSummary.failed/diagnostics`，不会从 `export_game_materials()` 直接抛出；可用 sidecar 会用于安全重建，未关联的已有输出保留。

4. **UIPackageTool 兼容 API**

   `UIPackageTool.split_atlas(source, ordinary_output_dir)` 恢复为始终输出到 `<ordinary_output_dir>/<base_name>/`，不再根据 output basename 猜测包目录。新增 `split_atlas_to_package_dir(source, package_dir)` 作为显式包目录入口，Preview Service 默认使用该入口；旧调用和新入口分别有回归测试，即使普通输出目录 basename 恰为包名也不会混淆。

5. **报告路径**

   Task 4 主报告中的 Burst Head 示例已统一为 `output/game_material/burst-head`。

## TDD 证据

1. 先新增顶层 metadata 键碰撞、manifest 保存失败后重试、三类损坏 manifest、旧/新 split API 测试。
2. 红灯验证得到 13 个预期失败：顶层键误分类、保存失败重试膨胀、损坏 manifest 未诊断、显式 API 缺失及 Service 未切换。
3. 实现最小修正后 focused suite 通过，并运行全量测试与 Ruff。

## 验证

- `E:\All-Projects\XL\.venv\Scripts\python.exe -m pytest -o addopts= tests/test_preview_material_catalog.py tests/test_fgui_atlas.py tests/test_preview_feature.py -q`：`24 passed`。
- `E:\All-Projects\XL\.venv\Scripts\python.exe -m pytest -o addopts= -q`：`290 passed`。
- `E:\All-Projects\XL\.venv\Scripts\python.exe -m ruff check app tests`：`All checks passed!`。
- `git diff --check`：通过。

## 文件

- `xl_updata_tool/app/features/preview/material_catalog.py`
- `xl_updata_tool/app/features/preview/fgui_atlas.py`
- `xl_updata_tool/app/features/preview/service.py`
- `xl_updata_tool/tests/test_preview_material_catalog.py`
- `xl_updata_tool/tests/test_fgui_atlas.py`
- `.superpowers/sdd/2026-09-17-preview-resource-workbench/task-4-report.md`
- `.superpowers/sdd/2026-09-17-preview-resource-workbench/task-4-fix2-report.md`

## Concern

受管环境对既有 pytest 临时目录/cache 目录和 Git index 有 ACL 限制；验证使用项目指定虚拟环境并在受控 elevated 执行下完成。未安装依赖，未修改无关目录或文件。
