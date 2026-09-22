# xl_qqbot

常驻服务：轮询消费 `xl_updata_server` 产出的角色图鉴长图，通过腾讯官方 QQ 机器人平台（qq-botpy SDK）分发到机器人所在的所有 QQ 群，成功后清理已分发文件。不发送私聊/C2C。

## 角色图鉴查询

群内 `@星落罗伯特 + 角色名` 查图鉴。匹配为多策略评分（`bot_app/matcher.py`，按优先级取最高分）：

| 分数 | 策略 |
|---|---|
| 100 | 官方中文名 / 别名 / 角色 ID 完全一致 |
| 98 | 英文名完全一致（大小写不敏感） |
| 96 | 拼音全拼一致（feinisi → 菲尼斯） |
| 94 | 拼音首字母一致（fns → 菲尼斯） |
| 90+ | 中文编辑距离高相似（difflib） |
| 85+ | 拼音高相似（同音字：菲尼丝/菲妮斯 ≈ 菲尼斯） |
| 80+ | 名称互相包含（只会进候选） |

判定：top1 ≥ 95 且领先第二名 ≥ 5 → 直接发图鉴；85~95 分差 ≥ 5 → 发图并附「已为你匹配到：<名字>」；70~85 或分差不足 → 回候选列表（最多 3 个，带星级，含「都不是」项，120 秒内回复数字选择，仅提问者本人有效）；top1 < 70 → 「未找到角色：<输入>」。

- 别名：`data/aliases.json` 人工维护（`{"中文名": ["别名", ...]}`），官方中/英文名自动内置。
- 纠错学习：用户从候选选定角色时记录到 `data/learned_aliases.json`（`{查询词: {角色ID: 次数, "users": [openid...]}}`）；同一查询词被 ≥2 个不同用户选定到同一角色 → 自动升级为该角色别名（不污染人工 aliases.json）。版本更新静音期对查询与选择都生效。
- 依赖 `pypinyin`（requirements.txt），缺失时拼音策略降级不可用、其余策略照常，启动记 warning。

## 进程架构：路由器 + 三级独立服务

一个机器人账号服务多个群，群按 [groups] 分三级。真实运行**四个互不影响的进程**，调试级崩溃不影响正式服务：

```
QQ 平台 websocket
      │
┌─────▼─────────────────────────────┐
│ 路由器  python -m bot_app.router   │  独占 QQ 网关（botpy）
│  · 群学习 / GroupTier 分级         │  主动推送管道（稳定基础设施）：
│  · 按群级别 HTTP 转发事件           │  Watcher(outbox+播报)、BilibiliWatcher
│  · muted 现读 update_status.json  │  （tiers 门禁过滤目标群，语义不变）
└─┬──────────┬──────────┬───────────┘
  │ :8781    │ :8782    │ :8783
┌─▼────┐ ┌───▼───┐ ┌────▼───────┐
│debug │ │ test  │ │ production │   python -m bot_app.service --tier <级> --port <口>
│服务   │ │ 服务   │ │ 服务        │   只处理图鉴查询/候选选择（QueryHandler）
└──────┘ └───────┘ └────────────┘   候选状态在本进程内存；QQSender 仅 HTTP API
```

- 事件 envelope：`POST /event`，`{"type": "group_message"|"group_at"|"group_add_robot", "muted": bool, "data": {<原始事件 dict>}}`；`muted=true` 时服务按现有静音语义忽略查询与选择。`GET /health` 返回 `ok`。
- 转发失败（服务未启动/超时/非 200）路由器只记 error 日志，不影响其他事件与推送。
- 端口/超时配置在 `[router]`；级别服务监听 127.0.0.1 仅本机可达。
- 部署：`scripts/install_router.sh` 为 debug/test 创建独立 git worktree（共享主项目 .venv，`PYTHONPATH` 指向各自 worktree）；`deploy/` 下四个 unit（router 50%/256M，服务各 30%/200M，均 Restart=on-failure、Nice=10）。
- 旧一体化入口 `python -m bot_app.main` 保留可用（单进程跑全部），部署上由上述架构取代。

## 群分级与功能门禁

群分三级（累积语义：功能级别 L 在群级别 G 开放 ⟺ G ≥ L）：

- `debug`（调试级）：全部功能
- `test`（测试级）：测试级 + 正式级功能
- `production`（正式级，默认）：仅正式级功能

配置：`[groups]` 的 `debug`/`test` 列表填群 openid（不在列表的群即正式级）；`[features]` 配置各功能所需最低级别（`character_query` @查图鉴含候选选择、`character_push` 新角色图鉴推送、`update_notice` 更新播报、`bilibili_watch` B 站监视推送），缺省/未知功能名均为 `production`，非法级别值启动报错。

语义要点：

- 事件类（查询、候选选择）在门禁外静默忽略（DEBUG 日志）；推送类按级别过滤目标群。
- `character_push` 过滤后无目标群时，该 outbox 批次视为无需发送并正常清理（不会卡 incomplete）；`bilibili_watch` 过滤后为空不推进已发状态（下轮自动重试）。
- 级别每次现算：把群从 debug 列表移到别处立即生效，无需重启。

## 与 xl_updata_server 的交接约定

- 上游 outbox：`/home/admin/xl_updata_server/data/outbox/<版本时间戳>/`
- 目录内容：
  - 若干 `<角色ID>_<角色名>_角色档案_长图.png`
  - 一个 `manifest.json`，字段示例：
    ```json
    {
      "version": "20260920120000",
      "generated_at": "2026-09-20T12:00:00+08:00",
      "characters": [
        {"id": "1", "name": "Alice", "file_name": "1_Alice_角色档案_长图.png"}
      ]
    }
    ```
- 没有新角色时 outbox 为空。
- 本服务只在**同一版本全部图片都成功发送**后删除该版本目录；失败图片会在下一轮重试。

## 配置

复制示例配置并填写凭据：

```bash
cp config.toml.example config.toml
# 编辑 config.toml
```

关键字段：

- `[bot].appid` / `[bot].secret`：QQ 开放平台机器人凭据。
- `[watch].outbox_dir`：上游 outbox 路径。
- `[watch].interval_seconds`：轮询间隔，默认 30 秒。
- `[target].group_openids`：可选覆盖。留空（默认）= 发给所有已知的机器人所在群；手填 = 只发这些群。
- `[target].auto_learn_from_events`：监听群事件自动登记群 openid，默认开启。
- `[upload].file_base_url`：图片公网 URL 前缀（可选）。
- `[message].template`：发送文字模板，支持 `{version}` `{name}` `{file_name}`。

## B 站动态监视

轮询监视多个 B 站 UP 主（配置 `[[bilibili.targets]]`）的**图文动态**与**视频投稿**两路，推送到所有已学习的 QQ 群。两种模式：

- `full`（星落官方号默认）：图文动态发「【星落官方动态】标题\n正文」+ 逐张原图（下载到 `data/bili_tmp/`，发送后清理）；视频发一行通知。
- `notice`：图文和视频都只发一行通知，不拉详情、不下图。

通知格式两路统一：

```
你关注的<名字>更新啦：<标题>
<链接>
```

动态链接 `https://www.bilibili.com/opus/<opus_id>`，视频链接 `https://www.bilibili.com/video/<bvid>`。

- 接口（服务器实测全通，均需登录态 SESSDATA；请求头用移动端 UA + `Referer: https://m.bilibili.com/space/<mid>`）：
  - 图文列表 `GET /x/polymer/web-dynamic/v1/opus/feed/space?host_mid=<mid>`（无需签名），`opus_id` 雪花 ID 越大越新，直接当排序/去重键；
  - 图文详情 `GET /x/polymer/web-dynamic/v1/opus/detail?id=<opus_id>`（无需签名，仅 full 模式拉取），`data.item.modules` 按 `module_type` 取标题/作者时间/正文段落（`para_type=1` 文字、`para_type=2` 图片）；
  - 视频列表 `GET /x/space/arc/search?mid=<mid>&ps=5`（**需 WBI 签名**），`data.list.vlist[]` 含 `bvid/title/created/author`；
  - UP 主名 `GET /x/space/acc/info?mid=<mid>`（**需 WBI 签名**），取 `data.name`。
  - 注意：旧版动态接口 `feed/space` 在机房 IP 上永远 412（带 SESSDATA 也无效），已弃用。
- UP 主名：`name` 配置非空直接用；留空则运行时解析（acc/info → 视频 vlist 的 author → mid 字符串兜底），解析成功缓存进 state 文件避免每轮请求。
- 去重：`data/bilibili_state.json` 结构 `{"targets": {"<mid>": {"last_opus_id", "last_video_created", "last_bvid", "name"}}}`；每目标独立基线，首轮以最新一条为准不补发，重启不重复。
- 旧版 state 文件（顶层 `last_opus_id/last_video_created/last_bvid`）自动迁移到默认官方号 mid 下，已发基线保留不重发；旧配置（`[bilibili].mid` 单目标）无 `targets` 时等价为单目标 full 模式。
- 每路每目标每轮最多推送 5 条，按时间升序，逐条推进 state，超出部分下一轮继续，防止历史洪水。
- 单条图文详情拉取/解析失败记 warning 跳过并推进 state（避免坏数据卡死队列）；某目标某路接口失败只跳过该路该目标，不影响其他，绝不崩溃。

配置项（`[bilibili]`）：

- `enabled`：开关，默认 `true`。
- `interval_seconds`：轮询间隔，默认 300。
- `sessdata`：登录态 cookie（**必填**，不带会被风控）。获取方式：

  浏览器登录 bilibili.com → 按 F12 打开开发者工具 → 「应用/存储」(Application/Storage) → Cookies → `https://www.bilibili.com` → 复制 `SESSDATA` 的值填入。

- `[[bilibili.targets]]`：`mid`（必填、去重）、`name`（留空自动解析）、`mode`（`full`/`notice`，默认 `full`）。

## 部署

在 Linux 服务器（与 `xl_updata_server` 同机同用户 `admin`）：

```bash
bash scripts/install_bot.sh
systemctl --user start xl-qqbot
systemctl --user enable xl-qqbot
journalctl --user -u xl-qqbot -f
```

`deploy/xl-qqbot.service` 已限制：

- `CPUQuota=50%`
- `MemoryMax=256M`
- `Nice=10`
- `Restart=on-failure`

## 分发记录

- `data/sent/<版本>/manifest.json`：原始 manifest 副本。
- `data/sent/<版本>/status.json`：每张图对每个群的分发状态。
- outbox 版本目录内的 `<file_name>.done`：该图已全群发送成功的原子标记。

进程重启后通过 `data/sent` 识别已分发内容，不会重复发送。

## qq-botpy API 用法说明

已核实的部分：

- SDK 初始化：`botpy.Client(intents=botpy.Intents(public_messages=True))`。
- 鉴权：使用 `AppID` + `AppSecret` 换取 `access_token`（SDK 内部封装）。
- 发送群消息：`POST /v2/groups/{group_openid}/messages`，`msg_type=7`，`media={"file_info": ...}`。
- 富媒体上传：`POST /v2/groups/{group_openid}/files`，`file_type=1`。
- SDK 1.2.1 的 `post_group_file` 仅支持 `url` 直传，不支持本地文件字节。
- **群列表 API：当前 QQ 开放平台 v2 没有提供“获取机器人所在群列表”的接口**。SDK 有 `/users/@me/guilds`（频道），没有 `/users/@me/groups`（群聊）。因此目标群唯一来源是事件学习（`GROUP_ADD_ROBOT`、`GROUP_AT_MESSAGE_CREATE`）和手填覆盖。

待实测的部分：

- 未配置 `file_base_url` 时使用的分片上传路径（`upload_prepare` -> PUT 分片 -> `upload_part_finish` -> `files` 合并）未在真实环境验证。
- `msg_type=7` 时 `content` 字段是否会在客户端显示为图片说明文字，需实测确认；如被平台忽略，可改为发送两条消息或调整 `msg_type`。
- 凭据、群 openid、图片上传链路都需要在填入真实值后验证。

## 本地图片上传的两种模式

1. **URL 直传（推荐）**：配置 `[upload].file_base_url`，让 outbox 目录可通过公网访问（如 nginx 静态目录），bot 把本地路径映射为 URL 后调用 SDK。
2. **分片上传**：不配置 `file_base_url` 时，bot 按官方文档直接走本地文件分片上传。该路径代码已按文档实现，但需凭据实测。

## 测试

```bash
python -m pytest -q
```

单测覆盖配置校验、outbox 扫描、防重、manifest 解析、watcher 成功/失败/部分失败路径；sender 已被 mock，不联网。
