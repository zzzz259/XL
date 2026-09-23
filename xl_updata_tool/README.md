# XL Update Tool

Windows 桌面资源更新与浏览工具。主流程是检查版本、下载 bundle、按导出类别导入 AssetStudio，再浏览或导出 Lua、音频、Spine 和游戏图片素材。

## 文档

- [文档地图](docs/README.md)
- [架构与运行目录契约](docs/架构与协作基线.md)
- [开发、调试与验证](docs/开发文档指南.md)
- [代码所有权与边界](docs/代码所有权与边界.md)
- [版本历史](docs/版本历史.md)

## 普通用户：运行 Release

Windows 10/11 x64。官方便携 Release 包解压后运行 `XL.exe`，Python、Java、.NET 运行时及桌面工具由发布包提供，不要求另装这些运行时。检查版本和下载需要网络；浏览已有本地产物可离线使用。

下载地址见 GitHub Releases。使用前先阅读压缩包随附说明；不要将 `data/`、`output/` 或 `logs/` 当作可随意删除的缓存，它们分别保存下载/处理中间数据、交付产物和诊断记录。

## 开发者：源码运行

要求 Windows 10/11 x64。桌面开发默认使用仓库根目录 `XL/.venv`；CI 用 Python 3.12 并在工具子项目创建隔离环境。启动脚本优先查找工具子项目 `.venv`，否则回退到仓库根 `.venv`。

```powershell
py -3.12 -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r xl_updata_tool\requirements-dev.txt
Push-Location xl_updata_tool
..\.venv\Scripts\python.exe main.py
Pop-Location
```

也可双击 `xl_updata_tool\run.bat` 或 `debug.bat`；它们按相对路径查找环境，不依赖用户机器绝对路径。正常模式使用 `run.bat`，诊断问题时用 `debug.bat`。构建便携版还需要 JDK 21（含 `jlink`），且构建脚本会下载/准备所需运行时，因此构建机需要联网。

## 构建 Windows 便携包

在仓库根目录用 PowerShell 执行：

```powershell
.\xl_updata_tool\scripts\release\build-release.ps1 -Version 2.0.0
```

脚本默认在 `xl_updata_tool/build/.venv` 创建隔离构建环境；`-NoVenv` 才使用当前 Python。默认会准备 Java 和 .NET 运行时、执行 pytest/Ruff、打包并自检。仅在复用已准备的运行时时使用 `-SkipRuntimes`；只有明确接受省略验证时才使用 `-SkipTests`。产物位于 `xl_updata_tool/release/`，构建目录及其报告位于 `xl_updata_tool/build/`。版本发布 workflow 见仓库根 `.github/workflows/release.yml`。

## 基本使用

1. 启动后检查版本，选择版本并下载所需 bundle。
2. 点击“导入 AS”，按需选择 Lua、角色立绘/Spine、FGUI 图集或音频类别。
3. 资源导入完成后，图片预处理会在后台完成 Spine 原始资源归档、图集切割和识别到的独立素材导出；切换图片预览页只读取已生成产物，不负责首次全量预处理。
4. 在预览页查看资源或执行 Spine 导出。默认设置按类型区分：`cardspine` 默认 PNG 与完整动画 MP4；`battlespine` 默认 `motion_stander` 表情且只导出 PNG；`eventcovers` 默认导出静态图。自定义模式提供传统导出设置。

主要交付目录：

| 路径 | 内容 |
|---|---|
| `output/lua/<版本>/` | 按版本保存的 Lua 成品 |
| `output/audio/` | 音频成品及增量/已读状态数据 |
| `output/spine/` | 按资源家族与来源指纹保存的 Spine 输入文件和预览索引 |
| `output/character/<角色ID>/` | 角色立绘、战斗小人静态图及立绘动画视频 |
| `output/game_material/` | 裁切后的 FGUI 图集、BurstHead、LotteryBg、PassportPic 等游戏素材 |
| `output/preview_state.json` | 图片资源未读状态 |

`data/material/` 是可重建的导入/预处理暂存区；最终图片、Spine 原始归档和游戏素材同时写入 `output/`，便于在系统文件管理器中直接检查。准确的路径与失败恢复规则见[架构文档](docs/架构与协作基线.md)。

## 验证

```powershell
Push-Location xl_updata_tool
..\.venv\Scripts\python.exe -m pytest
..\.venv\Scripts\python.exe -m ruff check tests app
Pop-Location
```

这些命令只验证测试覆盖范围和静态规则；不能替代真实 AssetStudio 导入、SpineViewer/FFmpeg 输出、GUI 交互及产物检查。PR 中应说明这些外部链路是否实际验证。详细约定见[开发文档指南](docs/开发文档指南.md)及[贡献指南](../CONTRIBUTING.md)。
