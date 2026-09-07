# Release 环境自包含改造（platform 公共区）影响说明

> 日期：2026-09-07　分支：feat/release-hardening
> 范围：`app/platform/`、`app/bootstrap/`、`app/ui/`、三个 Feature 的工具调用点、`tools/epic7_debank_v1_0/epic7_debank.py`

## 背景与目标

打包（PyInstaller）后的目标机器是干净 Windows：没有 Python、Java、.NET。
改造前代码里存在两类会在干净机器上静默失效的写法：

- 裸命令名调外部工具（`'java'` 起 JVM 反编译 Lua、AssetStudio 直接跑
  framework-dependent 的 exe 依赖系统 .NET 8）；
- 冻结模式下 `epic7_debank` 用系统 `python` 跑 QuickBMS `-S` 回调脚本。

本轮把所有外部工具与 bundled 运行时（`runtimes/java`、`runtimes/dotnet`）的
路径解析收口到 `app/platform/tool_locator.py`，并新增 `--self-check` 自检入口。

## 为什么必须在 platform 公共区做

- 同一工具被多个 Feature/Shell/Platform 模块调用（AssetStudio 有 4 处调用方、
  SpineViewerCLI 有 4 处、ffmpeg/java 各 2 处以上）；在任一 Feature 内解析都会
  让其他调用方绕过 bundled runtime，冻结布局约定（`sys._MEIPASS` 下的
  `tools/` 与 `runtimes/`）本身是平台层契约。
- `paths.py` 已承担 frozen/dev 分流；tool_locator 复用 `get_base_dir` 的上溯
  层级，避免第二份目录约定漂移。

## 契约变化：只新增，不改旧契约

新增（不改 output/data/logs 目录契约、Qt 信号、取消语义、命令参数、cwd、超时）：

- `app/platform/tool_locator.py`
  - `resource_root()`：frozen → `sys._MEIPASS`；dev → 项目根（复用 paths 层级）。
  - `ToolLocator.create()`（frozen dataclass）：`tools`/`runtimes` 属性，
    `java()`/`dotnet()`（bundled 优先；dev 回退 `shutil.which`；frozen 禁止回退，
    缺失抛 `ToolNotFoundError`，继承 `FileNotFoundError` 以兼容既有容错），
    `assetstudio_dll()`/`assetstudio_command()`（frozen 或有 bundled dotnet 时
    返回 `[dotnet, dll]`；开发机无 bundled 时回退 `[AssetStudio.CLI.exe]` 单元素，
    保持开发现状），`spineviewer_cli()`/`spineviewer_gui()`/`ffmpeg()`/
    `vgmstream()`/`quickbms()`/`fsb_extractor()`/`unluac_jar()`/`unluac_opmap()`，
    `dotnet_env()`（DOTNET_ROOT/DOTNET_ROOT_X64 指向 bundled runtime），
    `subprocess_env()`（os.environ 叠加 dotnet_env）。
- `RuntimeConfig.self_check` + `--self-check` 参数（argparse parse_known_args，
  未知参数仍入 extra_args）。
- `app/platform/self_check.py`：`--self-check` 时配置日志后逐项自检
  （资源根可读、data/output/logs 可写、PySide6/Pillow/UnityPy/Crypto/psutil
  可导入、java 实际执行 `-version`、dotnet 实际执行 `--list-runtimes` 且含
  `Microsoft.NETCore.App 8.0.`、8 个工具文件存在），打印 [PASS]/[FAIL] 报告、
  写入日志会话目录 `self-check.txt`，必要组件缺失退出码 1；不创建 QApplication。
  frozen(console=False) 下先 `AttachConsole(-1)` 附着父控制台。
- `ImportProcessor`/`ImportWorker` 新增可选 `as_env` 参数；`as_cli` 兼容
  字符串路径（旧调用方/测试不变）与命令前缀列表两种形态，内部归一为
  `as_command`（末位始终是 exe/dll 目标，供存在性检查与 cwd）。

## 工具路径收口清单（全部改走 ToolLocator）

- Java：`features/importer/lua_decrypt.py` 两处 `'java'` → `java()`。
- AssetStudio：`platform/bundle_parser.py`（2 处）、`ui/asset_browser.py`
  （2 处）、`bootstrap/shell_contribution.py` → `assetstudio_command()` +
  `subprocess_env()`；cwd 仍为 AssetStudio 目录（dotnet 调 dll 时 deps/native
  解析依赖该目录）。
- SpineViewerCLI：`preview/controller.py`、`preview/export_controller.py`
  （3 处）→ `spineviewer_cli()`；SpineViewer GUI：`ui/main_window.py` →
  `spineviewer_gui()`。
- ffmpeg：`preview/spine_adapter.py:get_ffmpeg_path()` → `ffmpeg()`。
- unluac.jar/opmap：`features/importer/processing.py` → `unluac_jar()`/
  `unluac_opmap()`。
- 环境检查 `platform/environment.py`：quickbms 修正为
  `epic7_debank_v1_0/_subcontractors/quickbms.exe`（原错查
  `tools/quickbms/`），java/dotnet 改经 locator，补 fsb_aud_extr/unluac/
  SpineViewerCLI/ffmpeg 检查项。

## 冻结模式 QuickBMS 回退的限制

`epic7_debank` 的 QuickBMS 阶段通过 `-S` 回调 `_epic7_defsb.py`，必须有一个
可执行 Python。冻结包内没有解释器，干净机器上系统 PATH 也没有 python。
本轮改为显式探测：frozen 且 `shutil.which("python")` 为 None 时记录 warning
并**跳过 QuickBMS 回退**（bank 标记 `quickbms_skipped_no_python` 失败），
vgmstream 直解与 fsb_aud_extr 兜底不受影响；dev 模式行为不变。
后续如需在冻结环境恢复 QuickBMS 回退，需随包嵌入 Python 或把回调脚本改写为
原生工具，超出本轮范围。

## 架构门禁

`tests/test_architecture.py` 新增文本扫描：app/ 生产代码（豁免
tool_locator.py）不得再出现裸 `'java'`/`"java"` subprocess 调用与
`AssetStudio.CLI.exe` 硬编码拼装。tools/、tests/ 不在扫描范围。

## 验证

- `python -m pytest -q`：172 passed。
- `python -m ruff check --no-cache tests app app/bootstrap app/shared`：通过。
- `python main.py --self-check`：19/19 通过（本机 bundled java/dotnet 已由
  构建脚本生成），报告写入 logs 会话目录。
- bundled dotnet 实际拉起 `AssetStudio.CLI.dll`：CLI 正常启动并输出用法。

## 遗留事项

- `runtimes/` 由构建脚本生成、不入库（已在仓库根 .gitignore 登记）。
- 打包侧（build.spec/scripts/）由并行任务负责，需按
  `_internal/tools` + `_internal/runtimes/{java,dotnet}` 布局收包。
