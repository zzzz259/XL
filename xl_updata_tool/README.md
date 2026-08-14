# XL Update Tool - 游戏更新管理工具

用于监控游戏更新、下载资源包、追踪版本变化，以及将资源导入 AssetStudio 浏览解析。

## 项目简介

本工具面向需要跟进游戏资源更新的场景，提供「检查更新 → 下载 bundle → 导入解析」的完整流程。

## 环境要求

- Windows 10/11 64 位
- Python 3.10 或更高版本（推荐 3.12）
- 已安装 pip

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

每行有三个操作按钮：

| 按钮 | 作用 |
|---|---|
| 增量下载 | 只下载相比上一版本有变化的文件（推荐） |
| 全量下载 | 下载此版本的全部文件（会弹窗确认） |
| 删除已下载 | 删除此版本已下载的本地文件 |

选中版本后，点击顶部「导入 AS」可浏览已下载的资源。

## 目录结构

```
xl_updata_tool/
├── README.md           本说明文件
├── main.py             程序入口
├── run.bat             一键启动脚本
├── requirements.txt    Python 依赖
├── build.spec          PyInstaller 打包配置
├── app/                源代码
│   ├── core/           核心逻辑（下载、解析、数据库）
│   └── ui/             UI 界面
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
