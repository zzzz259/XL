# XL

XL 是面向《星落》的多项目仓库，包含 Windows 桌面资源工具和 Linux 后端服务。2.0.0 是 `xl_updata_tool` 的正式发布版本；服务器和 QQ bot 是独立部署的配套项目。

## 子项目

| 子项目 | 用途 | 入口文档 |
|---|---|---|
| [`xl_updata_tool`](xl_updata_tool/README.md) | Windows 桌面端：检查版本、下载 bundle、解析 Lua/音频/图片与 Spine 资源，并导出到 `output/`。 | [快速开始与使用说明](xl_updata_tool/README.md) |
| [`xl_updata_server`](xl_updata_server/README.md) | Linux 后台更新抓取、角色数据版本化、角色图鉴卡片渲染及 outbox 生成。 | [服务端文档](xl_updata_server/README.md) · [配置与运维](xl_updata_server/docs/配置与运维.md) |
| [`xl_qqbot`](xl_qqbot/README.md) | QQ 群机器人：角色图鉴查询、更新图鉴分发、公告与 B 站动态监视。 | [Bot 文档](xl_qqbot/README.md) · [配置与运维](xl_qqbot/docs/配置与运维.md) |

三个子项目有各自的 Python 依赖、虚拟环境和测试；服务器与 bot 通过文件目录和配置约定集成，不是桌面工具的运行依赖。

## 文档导航

- [文档地图](docs/README.md)：项目内正式文档、读者与信息归属。
- [贡献与分支流程](CONTRIBUTING.md)：`debug` → `test` → `main`、PR、提交和发布约定。
- [项目规范](项目规范.md)：monorepo、Python 环境、文档和生成文件约定。
- [安全与凭据](SECURITY.md)：凭据、外部资源及安全问题报告。
- 更新工具：[架构与运行时契约](xl_updata_tool/docs/架构与协作基线.md)、[开发指南](xl_updata_tool/docs/开发文档指南.md)、[版本历史](xl_updata_tool/docs/版本历史.md)。

## 长期分支

- `debug`：日常开发集成。
- `test`：候选变更与发布前验证。
- `main`：稳定发布基线；Release 从此分支的版本 tag 或受限的手动流程产生。

短期任务分支用于 PR，合并后删除，不作为长期分支。详细流程及当前 CI 行为见 [CONTRIBUTING](CONTRIBUTING.md)。

## 仓库布局

```text
XL/
├── .github/workflows/       # CI 与桌面工具 Release 自动化
├── docs/                    # 仓库级文档索引
├── xl_updata_tool/          # Windows 桌面应用
├── xl_updata_server/        # Linux 更新服务
└── xl_qqbot/                # QQ 机器人
```

开始前请进入对应子项目 README。运行数据、凭据、虚拟环境和构建产物不要提交到仓库。
