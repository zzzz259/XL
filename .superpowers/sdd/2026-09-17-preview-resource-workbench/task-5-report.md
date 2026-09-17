# Task 5 报告：三分页预览工作台与 Spine 树

## 完成内容

- `PreviewPage` 提供“角色 Spine”“角色导出立绘”“游戏素材”三个分页，并保留既有 `image_list`、`character_filter`、`preview_progress`、`preview_status`、上下文菜单和双击信号属性。
- 新增 `PreviewSpineTree`，按角色 → 皮肤 → Spine 文件展示资源，节点保存 `role_id`、`skin_key`、`skel_path`、`atlas_path` 等稳定身份数据。
- 实现角色/皮肤/文件递归勾选、父级三态聚合、`selected_records()`、`set_selected_records()`、`mark_selected_read()` 及页面级语义选择信号。
- 游戏素材页可接收 `GameMaterialCatalog`，展示 Burst Head 与 `fgui/<package>` 图集分组，并在无数据时显示空状态。
- `build_preview_item()` 支持透传资源身份、fingerprint 和 `is_new` 元数据，不从文件名猜测新状态。
- 未接入 Task 6 导出生命周期，未修改 `PreviewController`、资源发现、身份模型或导出逻辑。

## 修改文件

- `xl_updata_tool/app/features/preview/page.py`
- `xl_updata_tool/app/features/preview/spine_tree.py`
- `xl_updata_tool/app/features/preview/item.py`
- `xl_updata_tool/tests/test_preview_page.py`
- `xl_updata_tool/tests/test_preview_spine_tree.py`
- `xl_updata_tool/tests/test_preview_item.py`
- `.superpowers/sdd/2026-09-17-preview-resource-workbench/task-5-report.md`

## 验证结果

使用 `E:\All-Projects\XL\.venv\Scripts\python.exe`：

- `QT_QPA_PLATFORM=offscreen python -m pytest tests/test_preview_page.py tests/test_preview_spine_tree.py tests/test_preview_item.py -q -p no:tmpdir`：7 passed。
- 共享 UI chrome、Preview feature 边界与兼容入口回归：3 passed。
- `python -m ruff check app tests`：通过。
- `python -m compileall -q app`：通过。
- `git diff --check`：通过。

仓库中其他 Preview 历史测试统一使用 pytest `tmp_path`。本机运行器对默认及重定向后的 pytest 临时根目录均返回 `WinError 5`，因此该组全量测试在 fixture setup 阶段被环境 ACL 阻断，未将其误报为功能通过。
