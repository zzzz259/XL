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

也可双击 `xl_updata_tool\run.bat` 或 `debug.bat`；它们按相对路径查找环境，不依赖用户机器绝对路径。正常模式使用 `run.bat`，诊断问题时用 `debug.bat`。构建便携版还需要 JDK 21（含 `jlink`）和支持 C# 12 的 .NET SDK（8 或更新版），且构建脚本会下载/准备所需运行时，因此构建机需要联网。立绘场景导出器通过 `scripts/build-portrait-scene-renderer.py` 编译，复用 tools 内程序集，不执行 NuGet restore；源码首次运行场景导出前也需编译此工具。发布包内置工具，用户无需 SDK。

## 构建 Windows 便携包

在仓库根目录用 PowerShell 执行：

```powershell
.\xl_updata_tool\scripts\release\build-release.ps1 -Version 2.0.1
```

脚本默认在 `xl_updata_tool/build/.venv` 创建隔离构建环境；`-NoVenv` 才使用当前 Python。默认会准备 Java 和 .NET 运行时、执行 pytest/Ruff、打包并自检。仅在复用已准备的运行时时使用 `-SkipRuntimes`；只有明确接受省略验证时才使用 `-SkipTests`。产物位于 `xl_updata_tool/release/`，构建目录及其报告位于 `xl_updata_tool/build/`。版本发布 workflow 见仓库根 `.github/workflows/release.yml`。

## 基本使用

1. 启动后检查版本，选择版本并下载所需 bundle。
2. 点击“导入 AS”，按需选择 Lua、角色立绘/Spine、FGUI 图集或音频类别。
3. 资源导入完成后，图片预处理会在后台完成 Spine 原始资源归档、图集切割和识别到的独立素材导出；切换图片预览页只读取已生成产物，不负责首次全量预处理。
4. 在预览页查看资源或执行 Spine 导出。默认设置按类型区分：`cardspine` 默认 PNG 与完整动画 MP4；`battlespine` 默认 `motion_stander` 表情且只导出 PNG。角色以同版 `BaseFashion` 皮肤配置为单位；活动以 `BaseActivityStageConfig.bg_main` 的精确 Prefab 和能确认的窗口播放逻辑为来源。普通活动封面导出 PNG；已确认的入场活动导出入场完成后的 PNG 与完整动画 MP4，顺序入场逐阶段导出。部件各自保留缩放、镜像、旋转和动画速度，配置/依赖缺失及不支持的材质仍显示诊断。自定义模式提供传统导出设置。

角色/Spine 单分类导入时可复用已发布的同版 Lua；版本上下文缺失时会明确报告跳过场景生成，不自动选择其他版本。场景导出文件名包含配置皮肤 ID 和版本，避免多个配置引用同一 Prefab 或跨版本导出时互相覆盖。

图片工作台按工程能力显示“可完整导出”“特效暂不支持”“缺少资源”“旧工程待更新”或“当前版本不适用”。选中工程后可更新同版工程；更新需要本地同版 AB、资源映射和 Lua，失败或取消保留旧工程。完整导出包含有证据的配置交互视频，叠加动作保留主轨道；可在导出设置中关闭交互视频。基础导出必须主动选择，文件名标记 `_basic`，不含粒子特效。原始 Spine 归档、FGUI 图集和独立图片继续使用各自入口，批量结果分别报告成功、基础导出、失败和取消。

已支持的粒子材质随工程保存 MainTex、发射参数、变换和层序，离开原始 AB 仍可合成。当前只支持经核验的两种材质配置；未知粒子模块或材质显示诊断。法线、发光、噪声和遮罩贴图不作为图片部件。游戏全局重力尚未确认的工程明确记录 Unity 默认重力假设，不补造场景灯光或 Bloom。

立绘导入先按同版配置和 Prefab 依赖筛选所需 TextAsset、Texture2D，再提取对应 Unity 对象。皮肤工程发布到 `output/portraits/<版本>/characters/<角色ID>/<配置皮肤ID>/`：`skin.json` 保存部件、层序、变换、动作序列、内部皮肤/表情及配置来源，每个 `part_*` 目录内直接保存 `.skel`、`.atlas`、`.png` 文件，便于查看器自动寻找同目录图集和贴图。Sprite 背景生成静态 Spine JSON 工程，并保留原图与生成参数；法线、发光、噪声等 Shader 辅助贴图不参与立绘工程。活动位于同版本的 `events/`，Event0005 按游戏顺序拆为四个阶段。整个 `output` 可搬迁，预览和导出优先读取包内相对路径，不依赖原 AB、AssetStudio 索引或素材缓存。场景导出器复用内置 Spine 渲染器，以正交 XY 投影应用每个部件的 Unity 变换和独立时钟。

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
