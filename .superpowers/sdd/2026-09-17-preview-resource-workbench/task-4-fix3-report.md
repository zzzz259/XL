# Task 4 第三修正轮报告：可靠持久化与来源冲突

## 状态

已完成，基于最终复审提交 `5b801006`。本轮实现、测试和报告将合并为一个修正 commit。

## 根因与修正

1. **源更新被旧 manifest key 遮蔽**

   旧实现按 source key 查 manifest，且只在 sidecar source 不在 manifest 时合并。源内容更新后，旧 fingerprint 仍占据该 source key，manifest 保存失败后的重试看不到新 fingerprint sidecar，于是按递增命名再生成副本。本轮将 sidecar 恢复索引改为 `(source, fingerprint)`，旧 fingerprint 的输出继续保留；当前新 fingerprint 优先复用同一 sidecar 目标，manifest 仅作为可重建聚合索引。

2. **逐条记录与原子恢复**

   每个 sidecar 加载时校验 version、source、fingerprint、output、sidecar 文件名和目标文件存在性；manifest 记录也校验 source key、字段和目标存在性。复制成功后先原子写 sidecar；sidecar 保存失败会清理本轮新目标。manifest 原子保存失败不会删除 sidecar，下一次运行可通过同一 `(source, fingerprint)` 找回同一目标，不产生第三份副本。

3. **结构合法但来源冲突**

   加载 manifest 与 sidecar 后建立 identity 和 output 两个反向索引：同一 source/fingerprint 指向不同 output，或不同 source/fingerprint 指向同一 output，均返回 `failed` 与包含 source/output 的 diagnostics，并跳过 Burst Head 导出，保留既有文件。manifest 与 sidecar 对同一目标但 source、fingerprint 或 output 不一致也会进入冲突诊断，不能静默报告 `exported=2`。

4. **兼容性**

   metadata 资源分类逻辑未改变；`split_atlas` 与显式 `split_atlas_to_package_dir` 入口未改变。本轮 focused 回归继续覆盖 metadata 顶层键碰撞、FGUI atlas 分割及 Service 默认 splitter。

## 自动化测试

- `test_burst_head_source_update_manifest_save_failure_retry_reuses_new_target`：先导出旧 source，再更新 source，在新 fingerprint 的 manifest 保存失败后重试；断言两次重试的 png/sidecar 文件集合与内容一致，只有旧目标和同一个新目标，失败诊断存在且最终恢复成功。
- `test_burst_head_manifest_save_failure_retry_does_not_duplicate_or_overwrite_sources`：多个来源的 manifest 保存失败后重试，断言 source 内容均保留且不会覆盖或膨胀。
- `test_burst_head_sidecar_save_failure_retry_leaves_no_partial_target`：sidecar 首次保存失败时断言不留下 png 或 sidecar 半成品，下一次只生成一个目标并成功建立 sidecar。
- `test_burst_head_manifest_rejects_multiple_sources_for_one_output`：结构合法的 manifest 把两个来源映射到同一 output，断言 `exported == 0`、`failed/diagnostics` 非空、既有 output 内容不变。

## TDD 证据

先加入三项第三轮回归：源更新重试与来源冲突分别按预期失败；sidecar 失败清理回归在现实现中已通过，作为保留行为的保护测试。随后实现按 fingerprint 的 sidecar 恢复与 output 反向冲突校验，三项测试全部通过。

## 验证

- `E:\All-Projects\XL\.venv\Scripts\python.exe -m pytest -o addopts= xl_updata_tool/tests/test_preview_material_catalog.py xl_updata_tool/tests/test_fgui_atlas.py xl_updata_tool/tests/test_preview_feature.py -q`：`27 passed`。
- `E:\All-Projects\XL\.venv\Scripts\python.exe -m pytest -o addopts= -q`：`293 passed`。
- `E:\All-Projects\XL\.venv\Scripts\python.exe -m ruff check xl_updata_tool/app xl_updata_tool/tests`：`All checks passed!`。
- `git diff --check`：提交前执行。

## 文件

- `xl_updata_tool/app/features/preview/material_catalog.py`
- `xl_updata_tool/tests/test_preview_material_catalog.py`
- `.superpowers/sdd/2026-09-17-preview-resource-workbench/task-4-report.md`
- `.superpowers/sdd/2026-09-17-preview-resource-workbench/task-4-fix3-report.md`
