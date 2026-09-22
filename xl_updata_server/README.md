# XL Update Server

Linux 后台版 XL 更新抓取工具。它不启动 GUI，也不开放 HTTP 端口，只把角色图鉴 Lua 数据和新角色的图鉴长图保存到 `data/`，供后续 QQ Bot 消费。

## 运行方式

目标系统为 Alibaba Cloud Linux 3，运行用户为 `admin`。服务器需要 Python 3.11+、Java 21 和相邻的 `xl_updata_tool` 源码中的 `app/`、`tools/lua/`。

```bash
cd /home/admin/xl_updata_server
bash scripts/install_server.sh
bash scripts/check_server.sh
systemctl --user enable --now xl-updata-server.service
systemctl --user --no-pager status xl-updata-server.service
```

如 SSH 登录会话结束后仍要保持用户服务运行，需要在有 sudo 权限时执行一次：

```bash
sudo loginctl enable-linger admin
```

## 渲染管线

角色图鉴长图不再使用 Pillow 自写绘制，而是直接复用用户确认的 HTML 渲染模块。

- HTML 来源：`character_archive_demo_108_latest.html`（单页应用 `<script>` 中的 `makeCanvasLongImage`）。
- Node 渲染器位于 `renderer/`，核心文件：
  - `renderer/card_image_renderer.js`：从 HTML 中逐字抽取的 Canvas 2D 绘图逻辑。
  - `renderer/render_batch.js`：批量渲染 CLI，一次 Node 进程渲染多个角色。
  - `renderer/package.json`：依赖 `canvas`（node-canvas v3）。
- 字体资源位于 `assets/fonts/`：
  - `NotoSansCJKsc-Regular.otf` → 注册为 `Microsoft YaHei` normal / `Georgia` italic 回退。
  - `NotoSansCJKsc-Bold.otf` → 注册为 `Microsoft YaHei` bold/900。
  - 当前未提供真正 Georgia 字体，后续可在 `assets/fonts/` 放置并修改 `render_batch.js` 的 `registerFonts()`。

服务器安装步骤（首次部署或重装系统后执行）：

```bash
cd /home/admin/xl_updata_server
bash scripts/install_server.sh      # Python 虚拟环境与 Python 依赖
bash scripts/install_renderer.sh    # Node 与 canvas 原生依赖（需要 sudo/root）
```

`config.toml` 的 `[paths]` 段可配置 `node_bin`（默认 `node`）。渲染进程默认使用 `--max-old-space-size=512` 以适配 2核1.8GB 的弱服务器。

> 注意：由于使用 Noto Sans CJK SC 替代原 HTML 中的微软雅黑，`measureText` 的字形宽度会有细微差异，可能导致自动换行与浏览器版存在 1~2 行差别。这属于预期内的字体度量差异。

## 调度

普通模式每小时检查一次。默认从 `2026-09-18T10:00:00+08:00` 起每 21 天在周五 10:00 进入密集模式，每分钟检查一次，最多 20 分钟；成功处理更新后提前结束。所有状态写入 `data/state.sqlite`。

## 版本隔离与产物

- `data/current_version.json`：当前已发布版本指针；更新发布采用临时目录完成后原子改名。
- `data/versions/<timestamp>/character_data/current.json`：该版本角色数据。
- `data/versions/<timestamp>/character_data/versions/<timestamp>.json`：同一份角色数据的版本快照。
- `data/versions/<timestamp>/character_cards/<角色ID>_<角色名>_角色档案_长图.png`：非首跑时只包含本次新增角色；首跑会建立基线并渲染全部角色，但**不会**写入 outbox，避免 QQ Bot 一次发太多图刷屏。
- `data/versions/<timestamp>/manifest.json`：版本统计，包括 `baseline` 标记、`lua_hashes`、`new_characters_count`、`character_cards` 成功/警告/失败明细、实际下载的 hash 列表。
- `data/catalogs/`：Data 分类清单缓存。
- `data/outbox/<timestamp>/`：仅当非首跑且本次有新增角色时生成，包含新增长图和 `manifest.json`（版本号、生成时间、新增角色 id/name/文件名列表）。首跑不生成 outbox。QQ Bot 轮询该目录，取走文件后自行删除；空目录可保留或清理，不影响后续版本。

服务器版只下载 Data 分类的 Lua AB 包，提取白名单图鉴 Lua 后单 JVM 批量反编译。一次更新失败时临时版本目录会被丢弃，历史已发布版本不会自动删除；发布前临时目录中的 AB 包与中间工作目录会被删除，发布后版本目录只保留 `character_cards`、`character_data` 和 `manifest.json`。

## 低并发完整生产验证

首次全量验收使用 systemd 管理的一次性任务，默认并行度为 1，并写入独立的 `data/full-production-<时间>/`，不会改变生产服务当前指针：

```bash
./scripts/run_full_production.sh
systemctl --user status xl-updata-full-production
journalctl --user -u xl-updata-full-production --no-pager
```

验证目录的 `manifest.json` 会给出角色数据、皮肤、长图成功数、警告数和失败明细。不要通过 `nohup`、shell `&` 或非交互 SSH 子进程启动该任务。
