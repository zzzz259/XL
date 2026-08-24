# Issue #45 验证记录

## 必要门禁

- `python -m pytest -q`
- `ruff check app tests`
- 全量 Python AST 解析
- Qt MainWindow 启动冒烟
- `git diff --check`

## 兼容性边界

- 未删除 `app/core`、旧 `app/ui`、旧 Worker 或旧导出入口。
- 未修改 `output/`、`data/`、日志目录、数据库结构和任务取消语义。
- 新增 Platform/Shared 入口只改变依赖方向，不改变对外函数和资源输出契约。
