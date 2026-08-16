# XL Update Tool - 游戏更新管理工具

用于监控游戏更新、下载资源包、追踪版本变化，以及将资源导入 AssetStudio 浏览解析。

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
   pip install PySide6 Pillow
   ```

   如果下载慢，可用国内镜像：

   ```bash
   pip install PySide6 Pillow -i https://pypi.tuna.tsinghua.edu.cn/simple
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

每行有三个操作按钮：

| 按钮 | 作用 |
|---|---|
| 增量下载 | 只下载相比上一版本有变化的文件（推荐） |
| 全量下载 | 下载此版本的全部文件（会弹窗确认） |
| 删除已下载 | 删除此版本已下载的本地文件 |

选中版本后，点击侧边「导入 AS」可浏览已下载的资源。

工具栏分两处：

**顶部视图栏**（切换当前显示界面）：

| 按钮 | 作用 |
|---|---|
| 版本列表 | 返回版本列表主界面 |
| 图片预览 | 浏览导出的角色立绘（Spine 动画）|
| 音频 | 解密并浏览游戏音频 |
| 角色 | 加载角色数据图鉴（读 `output/lua`，完整数据缓存到 `output/character_data`）|

**侧边功能栏**（操作 + 导出配置）：

| 控件 | 作用 |
|---|---|
| 检查更新 | 检查并列出可下载的版本 |
| 导入 AS | 将已下载的 bundle 解析导出到 data/material/（自动反编译 Lua）|
| 刷新 | 刷新版本列表 |
| 作者 | 查看作者信息 |
| 导出配置（4 个勾选）| lua / 角色立绘 / FGUI图集 / 音频，控制「导入 AS」只导出勾选的资源类型（默认全勾）|

### 调试模式

开发测试时用 `debug.bat`（或 `python main.py --debug`）启动，会多出一排「调试模式」工具栏：选择性导出（只导出 Lua / 只导出贴图）+ 清空各数据区域按钮。正常使用 `run.bat` 不会显示。

## 目录结构

```
xl_updata_tool/
├── README.md           本说明文件
├── main.py             程序入口
├── run.bat             正常启动脚本
├── debug.bat           调试模式启动脚本
├── requirements.txt    Python 依赖
├── build.spec          PyInstaller 打包配置
├── app/                源代码
│   ├── core/           核心逻辑（下载、解析、数据库）
│   └── ui/             UI 界面（workers/dialogs/features/views/adapters）
├── tools/              外部工具（AssetStudio 等）
├── docs/               开发文档
├── data/               运行时数据（版本数据库、下载的 bundle）
├── logs/               日志
└── output/             导出产物
```

## 常见问题

**Q: 启动报错 `No module named PySide6`**

A: 未安装依赖，执行 `pip install PySide6 Pillow`。

**Q: 提示找不到 `AssetStudio.CLI.exe`**

A: 确保 `tools/AssetStudio/` 目录完整存在。

**Q: 下载速度慢**

A: bundle 文件托管在 CDN 上，速度取决于网络环境。

**Q: 能离线使用吗**

A: 浏览已下载的资源和查看版本历史可以离线，但检查更新和下载 bundle 需要联网。
