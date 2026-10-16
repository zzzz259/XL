# XL QQ Bot

Linux 常驻 QQ 群机器人，基于腾讯官方 QQ 机器人平台和 `qq-botpy`。提供角色图鉴查询、普通角色复刻排期图片查询、服务端新角色图鉴分发、更新播报和 B 站动态/投稿通知；不发送私聊消息。

## 功能与架构

- 群内 `@机器人 + 角色名` 查询角色资料，支持中文名、别名、英文名和拼音匹配；歧义时让提问者选择候选。
- 群内 `@机器人 复刻` / `复刻表` 等查询服务端已发布的普通首次复刻排期图；Gateway 按显式群组归属交给唯一 Bot 实例。
- Watcher 轮询 `xl_updata_server` 的 outbox，逐群分发新增图鉴，整批成功后清理该版本 outbox；失败项保留重试状态。
- BilibiliWatcher 监视配置的多个 UP 主。`full` 模式发布图文详情/原图和视频通知；`notice` 只发布通知。接口、SESSDATA 和状态文件说明见[配置与运维](docs/配置与运维.md)。
- 一个 Gateway 进程持有唯一 QQ Gateway 登录，也拥有唯一 QQ HTTP 出站 sender。它只按 `router.group_instances` 将原始事件交给唯一实例，并转发实例发来的原始出站载荷；不执行业务规则。
- Bot worker、通知服务、后台 jobs 和 updater 都显式配置 `instance_id`。每个实例独立配置开关、目标群、端口、数据根目录和持久通知队列；同一份代码可以运行任意多个实例。
- Bilibili、游戏状态监控和 updater 定时轮询由对应实例的配置开关控制。部署分支只决定代码快照，不改变功能集合或消息授权。
- 所有内部端口只绑定 `127.0.0.1`。实例部署清单和现有部署布局见[配置与运维](docs/配置与运维.md)及 [xl_deploy 运维指南](../xl_deploy/README.md)。
- `bot_app.main` 是旧版单进程兼容入口，包含自己的 QQ 登录和 HTTP sender，不能与 Gateway 同时运行；多实例部署只使用 `GatewayRuntime` 与实例 worker/service units。

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
.venv/bin/python -m unittest discover -s tests -p '*_unittest.py' -v
```

测试对 sender/外部请求使用 mock，不验证真实 QQ 凭据、群授权、媒体上传、B 站风控或生产网络。配置和实际部署步骤见[配置与运维](docs/配置与运维.md)。

## 配套项目

本 bot 消费 `xl_updata_server/data/outbox/`，并读取该服务发布的角色数据/版本目录。需让 bot 配置中的共享路径与服务端配置一致；两者没有直接 HTTP API。拓扑和交接契约变更应同时更新[服务端运维文档](../xl_updata_server/docs/配置与运维.md)。仓库全局入口见[文档地图](../docs/README.md)。
