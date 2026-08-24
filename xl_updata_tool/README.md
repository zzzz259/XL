# XL Update Tool - 游戏更新管理工具

用于监控游戏更新、下载资源包、追踪版本变化，以及将资源导入 AssetStudio 浏览解析。

## 文档导航

- 本文件：面向使用者的安装、启动、导入、功能说明和常见问题。
- [开发文档指南](docs/开发文档指南.md)：面向开发者的架构、模块职责、调用链、运行目录契约和验证方法。
- [版本历史](docs/版本历史.md)：按新版本到旧版本记录功能和修复变更。
- [架构与协作基线](docs/架构与协作基线.md)：多人协作边界、失败恢复要求和提交前检查。
- [代码所有权与边界](docs/代码所有权与边界.md)：功能域认领范围、公共架构区和迁移期修改规则。
- [开发过程记录](docs/worklog/)：任务计划、调查发现和进度记录，仅供开发过程追踪。

## 项目简介

本工具面向需要跟进游戏资源更新的场景，提供「检查更新 → 下载 bundle → 导入解析」的完整流程。

## 环境要求

- Windows 10/11 64 位
- Python 3.10 或更高版本（推荐 3.12）
- 已安装 pip
- Java 21+（Lua 反编译用，需在 PATH 中）
- .NET 8 运行时（AssetStudio 用）

## 安装步骤

1. 克隆或解压本仓库到任意目录。

2. 打开命令行（cmd 或 PowerShell），进入项目目录：

   ```bash
   cd xl_updata_tool
   ```

3. 安装依赖（只需执行一次）：

   ```bash
   pip install -r requirements.txt
   ```

   如果下载慢，可用国内镜像：

   ```bash
   pip install -r requirements.txt -i https://pypi.tuna.tsinghua.edu.cn/simple
   ```

4. 运行：

   ```bash
   # 方式一：双击 run.bat
   # 方式二：命令行执行
   python main.py
   ```

## 使用说明

首次启动会自动检查更新，之后在顶部工具栏点击「检查更新」即可。

主界面是一个版本列表，每行显示：

```
版本日期 | 下载状态 | Bundle 数量 | 备注 | [操作按钮]
```

其中「备注」列显示该版本相对上一版本的差异：`新增 X | 移除 Y | 未变 Z`。

开发者请先阅读：[架构与协作基线](docs/架构与协作基线.md)。其中记录了 core/ui 边界、运行时目录契约、失败恢复要求和后续拆分路线。

旧版资源浏览器仅作为兼容入口保留，正常导入流程使用主界面的 AssetStudio 后台任务。
版本列表刷新时会以 `data/bundles/` 的实际文件校准数据库下载状态。
Lua 成品按版本留存在 `output/lua/<版本时间戳>/`，不会再把多个版本合并到同一个目录；`data/material/assets/lua/` 只作为导出过程中的临时目录，Lua 成功发布后会自动清理。
音频导出完成后会自动执行后处理，`data/material/assets/fmodassets/`、debank 输入目录和 `output/audio/.debank-temp/` 只作为临时中间态并在流程结束后清理；最终音频增量写入 `output/audio/`，不会按版本拆目录。`output/audio/.bank_state.json` 保存 bank 来源指纹、分类目录和最终文件清单，未变化且成品完整的 bank 会跳过重复解密，分类变化或成品缺失会自动重新处理。debank 对每个 bank 使用隔离临时目录并默认 6 路受控并行，优先由 vgmstream 直接读取 bank，失败时再回退 QuickBMS/FSB 链路；旧分类清理使用一次性文件索引，避免大批量导出反复递归扫描；同盘成品优先移动、跨盘自动复制。日志会分别记录成功、空产出、失败和复制失败，便于定位活动音乐缺口；完成后还会对照最新 Lua 的 BaseSound/BaseSoundChapter 与 bank 状态，报告缺失 BGM 和配置表之外的未分类 BGM。语音成品按角色和语言分别写入 `voice/<角色ID>/cn`、`voice/<角色ID>/jp`，中日同名文件不会互相覆盖或删除；旧的错位角色文件会在重新导出时清理，提取器生成的连续重复事件后缀会归一化；文件夹层级会沿未读音频递归显示红色“新”标记。点击处理弹窗的“取消”会终止当前 bank 解包及其子进程，保留已经发布的最终文件，不会自动续跑。仅勾选音频导出时，若版本存在 `assets_map.json`，程序会只把含音频资源的 AB 包交给 AssetStudio。
vgmstream 直解 bank 或 FSB 失败时，流程会回退到旧 QuickBMS/`fsb_aud_extr.exe`；回退成功仍计为成功，失败会在日志中记录 bank 级状态，不再被 QuickBMS 的 0 退出码掩盖。
角色数据仓库位于 `output/character_data/`：每个已解析版本保留一份快照，当前角色数据做增量合并，新角色和数值变化会在角色列表及“角色”顶部标签显示“新”角标，打开详情后清除。应用启动和切换角色页优先读取本地仓库/缓存，不会因为切换页面现场解析 Lua；可在角色页点击“开始解析”主动刷新，或由最新 Lua 导出完成后自动触发。
角色详情展示和 CSV 导出由无 Qt 的 `app/core/character_presenter.py` 负责，便于测试和后续扩展。

每行有三个操作按钮：

| 按钮 | 作用 |
|---|---|
| 增量下载 | 只下载相比上一版本有变化的文件（推荐） |
| 全量下载 | 下载此版本的全部文件（会弹窗确认） |
| 删除已下载 | 删除此版本已下载的本地文件 |

选中版本后，点击侧边「导入 AS」可浏览已下载的资源。

工具栏分两处：

当前正式界面采用统一的蓝灰深色主题，历史主题配置会自动兼容迁移；图片预览、音频和角色页面共享一致的页头、操作栏、状态栏和空状态结构。角色详情以 Wiki 分区展示，技能数字支持一键高亮；图片预览显示总数与选择数，音频页在空目录时提供下一步提示；图片查看器、角色选择和导出设置对话框也复用同一套视觉令牌。

**顶部视图栏**（切换当前显示界面）：

| 按钮 | 作用 |
|---|---|
| 版本列表 | 返回版本列表主界面 |
| 图片预览 | 浏览导出的角色立绘（Spine 动画）|
| 音频 | 浏览、播放和导出已自动解析的游戏音频；新导出音频显示红色“新”状态 |
| 角色 | 优先读取本地角色仓库/缓存；手动“开始解析”时加载 `output/lua/<版本>/`，数据仓库位于 `output/character_data/` |

**侧边功能栏**（操作 + 导出配置）：

| 控件 | 作用 |
|---|---|
| 检查更新 | 检查并列出可下载的版本 |
| 导入 AS | 将已下载的 bundle 解析导出；仅勾选 Lua 或音频且存在资源映射时只将命中的 AB 交给 AssetStudio；Lua 按版本写入 `output/lua/<版本>/`，音频自动解析并增量写入 `output/audio/`，成功后清理各自临时目录；其他分类写入 `data/material/`（失败或取消时保留旧产物）|
| 刷新 | 刷新版本列表 |
| 作者 | 查看作者信息 |
| 导出配置（4 个勾选）| lua / 角色立绘 / FGUI图集 / 音频，控制「导入 AS」只导出勾选的资源类型（默认全勾）|

### 调试模式

开发测试时用 `debug.bat`（或 `python main.py --debug`）启动，会多出一排「调试模式」工具栏：选择性导出（只导出 Lua / 只导出贴图）+ 清空各数据区域按钮。Debug 启动会额外记录任务 ID、阶段、外部工具退出信息和环境摘要；日志位于 `logs/<session_id>/`，包括 `app.log`、`error.log`、`debug.log` 和 `environment.txt`。未捕获的主线程/工作线程异常会在同一目录生成 `crash_*.log`。正常使用 `run.bat` 不会显示调试工具栏，也不会写入 DEBUG 级别日志。

## 目录结构

```
xl_updata_tool/
├── README.md           本说明文件
├── main.py             程序入口
├── run.bat             正常启动脚本
├── debug.bat           调试模式启动脚本
├── requirements.txt    Python 依赖
├── requirements-dev.txt 开发与测试依赖
├── pyproject.toml      pytest 与 Ruff 配置
├── build.spec          PyInstaller 打包配置
├── app/                源代码
    │   ├── bootstrap/      迁移期应用上下文与 Feature 装配入口
    │   ├── shared/         与 Qt 无关的跨 Feature 契约
    │   ├── features/audio/ 音频 Feature 页面、控制器、目录服务、Worker 和树逻辑（P1）
    │   ├── features/characters/ 角色 Feature 页面、控制器和 Qt-free 数据服务（P2）
    │   ├── features/versions/ 版本工作区、更新检查、下载计划和 Bundle 状态（P3）
    │   ├── features/importer/ 导入规格、精准 Bundle 筛选和后处理结果（P4）
    │   ├── features/preview/  图片预览页面、目录服务、加载/导出控制（P5a）
    │   ├── core/           迁移期核心逻辑兼容层
    │   └── ui/             迁移期 UI 界面（workers/dialogs/features/views/adapters）
├── tests/              项目测试（与 app/ 平级）
├── tools/              外部工具（AssetStudio 等）
├── docs/               开发文档
├── data/               运行时数据（版本数据库、下载的 bundle）
├── logs/               日志
└── output/             导出产物
    ├── lua/             反编译后的 Lua，按版本时间戳分目录
    ├── character_data/  角色当前数据、历史快照和未读变化状态
    └── audio/            音频最终产物与未读状态
```

## 常见问题

**Q: 启动报错 `No module named PySide6`**

A: 未安装依赖，进入 `xl_updata_tool/` 后执行 `pip install -r requirements.txt`。

**Q: 提示找不到 `AssetStudio.CLI.exe`**

A: 确保 `tools/AssetStudio/` 目录完整存在。

**Q: 下载速度慢**

A: bundle 文件托管在 CDN 上，速度取决于网络环境。

**Q: 能离线使用吗**

A: 浏览已下载的资源和查看版本历史可以离线，但检查更新和下载 bundle 需要联网。

### 开发验证

进入 `xl_updata_tool/` 目录安装开发依赖后，可运行：

```bash
python -m pip install -r requirements-dev.txt
python -m pytest -q
python -m ruff check --no-cache tests app/core app/ui app/bootstrap app/shared
python -B -c "from pathlib import Path; files=list(Path('app').rglob('*.py'))+list(Path('tests').rglob('*.py')); [compile(p.read_text(encoding='utf-8'), str(p), 'exec') for p in files]; print(f'Compiled {len(files)} files')"
```

提交 PR 前还应执行一次导入 smoke，并人工确认正常启动、AssetStudio 导入和 Lua 导出流程；完整验收清单见 [架构与协作基线](docs/架构与协作基线.md) 和 [代码所有权与边界](docs/代码所有权与边界.md)。
