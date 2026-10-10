# XL Update Server

Linux 后台更新服务，不启动桌面 GUI。生产版按计划抓取游戏更新；test 版强制关闭 CDN 定时轮询，但保留认证的本机手动控制 API。服务处理 Lua/角色数据、生成角色图鉴长图与普通首次复刻排期，并通过文件目录交给 `xl_qqbot`。

## 运行环境

- 生产目标：Alibaba Cloud Linux 3 / systemd user service。
- Python 3.11 或 3.12；独立使用本子项目 `.venv`。
- Java 21，用于 Lua 反编译。
- Node.js 20+ 和 renderer 原生依赖，用于生成角色图鉴长图。
- `../xl_updata_tool/tools/lua/unluac.jar` 与 opcode map；默认配置指向相邻的更新工具目录。

## 本地运行与测试

```bash
cd xl_updata_server
python3.11 -m venv .venv
.venv/bin/python -m pip install -r requirements.txt
cp config.toml.example config.toml
.venv/bin/python run_server.py --config config.toml --once
.venv/bin/python -m unittest discover -s tests -p '*_unittest.py' -v
```

`--once` 执行一次更新流程后退出；不带 `--once` 时进入常驻调度循环。单次运行会访问真实 CDN 并写入 `data/`，执行前确认配置和数据目录。

## 部署和配置

请先阅读[配置与运维](docs/配置与运维.md)，其中记录配置字段、依赖安装、systemd、调度、产物保留、故障排查及与 bot 的交接。现有脚本和 unit 默认针对 `/home/admin/xl_updata_server`；其他目录部署时需一并调整配置与 service 路径。

正式部署通过根目录 `xl_deploy` 管理：`test` 和 `main` 各有独立 unit、配置、API token、state.sqlite、下载/版本/角色数据及 Bot outbox。test 的 API 默认在 loopback `8791`，main 在 loopback `8790`；不要开放防火墙端口。debug 分支不部署运行时。test 的 `/api/v1/updates/run-once` 是唯一受支持的按需 CDN 处理入口，需 bearer token；测试环境不会因代码部署、服务启动或计划定时器自动处理游戏更新。

## 交付边界

- `data/current_version.json` 指向当前已发布的数据版本。
- `data/versions/<版本>/` 保存角色数据、manifest 和图鉴卡片。
- `data/versions/<版本>/rerun_schedule/` 保存与版本绑定的排期 JSON/PNG 不可变快照；`data/rerun_schedule/` 是当前稳定镜像。
- 非首次运行且有新增角色时，`data/outbox/<版本>/` 暂存待分发卡片和 manifest；首次运行只建立基线。
- `xl_qqbot` 在整批卡片成功分发后清理对应 outbox 版本目录。两者通过共享文件目录交接，不通过 HTTP API 通信。

服务事实以 `server_app/`、`config.toml.example` 和部署脚本为准。其他项目入口见[仓库文档地图](../docs/README.md)。
