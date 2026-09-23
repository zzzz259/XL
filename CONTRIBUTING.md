# 贡献指南

本仓库按 `debug`、`test`、`main` 三条长期分支协作。短期任务分支只服务于 PR，合并后删除。

## 分支晋级

```text
codex/<task> ──PR──> debug ──验证后 PR──> test ──发布验收 PR──> main ──tag──> Release
```

- `debug`：开发集成分支。新功能、修复和文档任务的 PR 默认以 `debug` 为目标。
- `test`：候选验证分支。从 `debug` 提升经集成检查的变更；在此进行端到端、部署或用户验收。
- `main`：稳定发布分支。仅从 `test` 提升已验收内容；发布 tag 只能指向 `main` 上的提交。
- `codex/<简短任务名>` 或 `<类型>/<简短任务名>`：短期任务分支，从当前目标阶段分支创建。不要长期维护已合并分支。
- 不直接 push 三条长期分支。所有晋级都通过 PR，写明范围、风险、验证结果和迁移/回滚要点。

示例（以下分支名按实际任务替换）：

```powershell
git switch debug
git pull --ff-only origin debug
git switch -c codex/update-docs
# 修改、验证、提交
git push -u origin codex/update-docs
# 创建 PR：codex/update-docs -> debug
```

集成后再按晋级关系开 PR：`debug -> test`、`test -> main`。确认 PR 已合并且目标分支包含提交后，删除短期分支。只有确实需要的已合并发布修复可走例外流程，并在 PR 说明原因。

## Pull Request 与提交

PR 描述至少包括：

- 改动目的和用户可见结果；
- 改动子项目、关键文件及兼容性/数据影响；
- 实际执行的验证命令和结果；
- 未验证的部分、环境限制、部署风险及回滚方法。

提交标题用清晰前缀，例如 `feat:`、`fix:`、`refactor:`、`docs:`、`test:`、`build:`、`chore:`。一个提交聚焦一类相关改动；不要把凭据、运行数据、缓存或用户生成内容纳入提交。

## 本地验证

开发环境的依赖安装到对应项目的 `.venv`，避免跨项目依赖污染。桌面工具的仓库级 `.venv` 是当前约定；服务端、Bot 的本地开发环境各自独立。Bot 生产部署中，debug/test worktree 按现有 systemd unit 共享主 Bot 的 `.venv`，不要把这项部署约定误当成本地开发要求：

```powershell
# Windows 桌面工具：仓库根目录 XL\.venv
Push-Location xl_updata_tool
..\.venv\Scripts\python.exe -m pytest
..\.venv\Scripts\python.exe -m ruff check tests app
Pop-Location

# Linux 服务端：在 xl_updata_server/.venv 创建开发/运行环境
# Linux Bot 本地开发：在 xl_qqbot/.venv 创建环境；生产 worktree 的共享方式见 xl_qqbot/docs/配置与运维.md
```

服务端与 bot 的安装、测试命令见各自 README。GUI、资源导入、外部 CLI 和生产部署等无法由单元测试代表的路径，PR 必须列明实际人工/集成验证情况；不得把未运行的验证写成通过。

CI 配置位于 `.github/workflows/`。本地变更分支、PR 目标和自动化触发范围必须保持一致；分支保护规则需在 GitHub 仓库设置中单独启用，workflow 本身不等于分支保护。

## 文档与仓库卫生

- 先查 [文档地图](docs/README.md)，在对应子项目的正式文档中维护事实，避免复制同一份长说明。
- 发布行为写入版本历史；未发布的想法留在 Codex 工作区或 Issue，不要伪装成当前行为。
- 不编辑随工具带入的第三方 README/许可证/说明，除非任务明确是供应商升级；不要把工具历史日志当成 XL 运行日志。
- 临时计划、审计笔记、实验脚本及测试输出放在 Codex 工作区或项目忽略的运行目录，不要新增根目录杂项。
- 提交前检查完整 diff、内部链接、忽略规则和 `git status`，确认没有密钥、AB 包、输出文件、缓存、个人路径或无关格式化。
