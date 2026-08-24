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
