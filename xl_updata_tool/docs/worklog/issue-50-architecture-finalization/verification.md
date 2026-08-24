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

## P2 退出门

- [x] 五个 Feature 工厂可在隔离 `AppContext` 中创建。
- [x] `FeatureRuntimeRegistry` 覆盖顺序、重复 key、页面激活、status/progress/badge binding 和逆序关闭。
- [x] shared/bootstrap 保持 Qt-free；signal 作为 opaque port 传递。
- [x] P2 全量 pytest 156 passed、Ruff、AST 156、import smoke、Qt offscreen 和 diff check 通过。
- [x] 生产启动仍走旧装配路径，未把 P2 装配变化误认为用户行为变化；正式切换留到 P3。

## P3 退出门

- [x] 生产 `main.py` 显式创建 AppContext、ApplicationRuntime 并传入 MainWindow，日志配置顺序保持不变。
- [x] MainWindow 无具体 Feature import/constructor；导航从 descriptor 生成。
- [x] ImportResult → Audio/Characters 后处理由 Composition Root workflow 显式路由，覆盖成功、取消和音频失败路径。
- [x] 159 项全量 pytest、Ruff、AST 159、import smoke、runtime Qt offscreen smoke 和 diff check 通过。
- [x] 既有 MainWindow 作为迁移期公共入口仍可无 runtime 参数启动，兼容旧测试/脚本调用。

## P4 退出门

- [x] Audio processing 不依赖 PySide6，Feature Worker 不依赖旧 `app.ui.workers.audio_decrypt`。
- [x] 旧音频入口保留为薄兼容转发，没有第二份处理实现；删除前的引用归零条件仍未满足。
- [x] bytes/bank 筛选、debank、分类、增量路径索引、语音命名修正、旧产物清理、BGM 审计和取消检查均保留在单一处理器中。
- [x] 音频处理、Audio Feature、Audio Repository、专辑映射、epic7_debank 和架构边界针对性测试通过。
- [x] P4 全量 pytest 133 passed、Ruff、AST 160、import smoke、runtime Qt smoke 和 diff check 通过；首次 Qt smoke 的真实数据库目录权限失败已改用任务临时目录复核通过。
