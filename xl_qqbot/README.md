# XL QQ Bot

Linux 常驻 QQ 群机器人，基于腾讯官方 QQ 机器人平台和 `qq-botpy`。提供角色图鉴查询、服务端新角色图鉴分发、更新播报和 B 站动态/投稿通知；不发送私聊消息。

## 功能与架构

- 群内 `@机器人 + 角色名` 查询角色资料，支持中文名、别名、英文名和拼音匹配；歧义时让提问者选择候选。
- Watcher 轮询 `xl_updata_server` 的 outbox，逐群分发新增图鉴，整批成功后清理该版本 outbox；失败项保留重试状态。
- BilibiliWatcher 监视配置的多个 UP 主。`full` 模式发布图文详情/原图和视频通知；`notice` 只发布通知。接口、SESSDATA 和状态文件说明见[配置与运维](docs/配置与运维.md)。
- 部署使用一个网关路由器加 `debug`、`test`、`production` 三个独立本地 HTTP 服务进程。路由器独占 QQ Gateway，按群级别转发事件；三个服务监听回环地址，默认端口分别为 8781、8782、8783。
- `bot_app.main` 保留旧的一体化兼容入口；新部署和分级隔离以 router + service units 为准。

## 本地环境、配置和测试

要求 Linux/macOS 或支持 asyncio 的 Python 3.11+ 环境。每个子项目使用自己的 Python 虚拟环境：

```bash
cd xl_qqbot
python3.11 -m venv .venv
.venv/bin/python -m pip install -r requirements.txt
cp config.toml.example config.toml
# 编辑 config.toml 后启动兼容的一体化本地进程：
.venv/bin/python -m bot_app.main
```

自动化测试：

```bash
.venv/bin/python -m pytest -q
```

测试对 sender/外部请求使用 mock，不验证真实 QQ 凭据、群授权、媒体上传、B 站风控或生产网络。配置和实际部署步骤见[配置与运维](docs/配置与运维.md)。

## 配套项目

本 bot 消费 `xl_updata_server/data/outbox/`，并读取该服务发布的角色数据/版本目录。需让 bot 配置中的共享路径与服务端配置一致；两者没有直接 HTTP API。拓扑和交接契约变更应同时更新[服务端运维文档](../xl_updata_server/docs/配置与运维.md)。仓库全局入口见[文档地图](../docs/README.md)。
