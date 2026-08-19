# Progress Log

## Session: 2026-08-19

### Current Status
- **Phase:** 3 - 页面视觉迁移（角色 Wiki 详情首版）
- **Started:** 2026-08-19

### Actions Taken
- 已读取并启用 XL 协作流程、Planning with Files、grill-me、完成前验证规则。
- 已在 `xl_updata_tool/docs/worklog/` 创建 `task_plan.md`、`findings.md`、`progress.md`，作为项目开发过程档案。
- 已确认当前分支为 `chore/architecture-stabilization`，初始工作区干净。
- 已完成第一轮源码目录盘点，确认 UI 代码集中在 `xl_updata_tool/app/ui/`。
- 已读取 `theme.py` 并搜索全局/局部 QSS：确认现有三主题、颜色替换生成机制和大量页面内联样式。
- 初步识别出主题系统、页面内联样式和 `main_window.py` 共享职责之间的耦合风险。
- 已梳理主窗口布局：顶部视图栏、左侧动作栏、中央多视图容器、底部状态栏；确认主题选择和下载进度目前与工具栏强耦合。
- 已确认当前默认主题为 `old`，主题切换存在重启提示；共享面板和四个主要页面均有重复的局部样式实现。
- 已完成 1440×900 离屏基线截图：确认蓝灰主题下存在大面积空置、层级不足、强调色过多、工具栏职责混杂和表格空间利用率低等视觉问题。
- 已完成图片预览、音频、角色三页离屏截图：确认三个页面共享“多条色带 + 细边框 + 空白内容区”模式，空状态、图标策略和局部工具栏样式不统一。
- 已完成旧/蓝灰/浅色三主题对比截图：确认旧主题只是强调色不同，浅色主题存在局部深色 QSS 混入，蓝灰主题最适合作为唯一主线。
- 已检索 Qt 官方样式文档、QDarkStyleSheet、PyQt-Fluent-Widgets 及 PySide6 专用 Skill；最终选择只参考 Qt 原生机制和 `pyside6-fluent-ui` 的桌面迁移方法。
- 已安装 `pyside6-fluent-ui` 到 Codex 全局技能目录并完整读取相关参考资料；未向 XL 仓库添加该 Skill 或第三方 UI 运行时。
- 已运行该 Skill 自带 `validate_skill.py` 和 `check_contrast.py`，结构校验及 light/dark/workbench 对比度检查均通过。
- 已按 XL 协作流程创建 Issue #27，内容包含背景、范围、非目标、第一阶段文件边界和逐条验收标准；尚未修改生产代码。
- 已开始 Issue #27 第一阶段：主题模块切换为克制蓝灰语义色板，增加 QPalette 基础色板和正式主题兼容映射。
- 已开始主窗口壳层调整：品牌区、侧边栏宽度/层级、主操作语义、主题兼容显示、工具提示和可访问名称。
- 已修复主题基础运行时遗漏：QSS 模板参数和 QPalette 交替行令牌；重新启动离屏主窗口成功，主题入口为单一 `new` 正式主题。
- 已生成第一阶段截图 `E:\AI-Agent\xl_ui_phase1_shell.png`：紫色品牌消失、蓝灰层级统一、主操作按钮语义更清楚；版本表格信息架构和大面积空白保留到下一阶段。
- 首次全量 pytest 被系统默认临时目录权限阻断（`WinError 5`，未进入测试断言）；使用明确可写的 `E:\AI-Agent\xl-pytest-basetemp` 重跑后，33 项测试全部通过。
- 用户已正常启动验证第一阶段：蓝灰主题、XL/资源工作台品牌、更新/导入/页面切换、单一主题入口和无重启提示均正常。
- 新增 `app/ui/widgets/view_chrome.py`，统一图片、音频、角色页的页头、操作栏、状态和空状态；页面标题与操作按钮移除 emoji。
- 版本列表新增工作区说明与摘要，并将版本工作区整体显隐封装为 `_set_version_content_visible()`。
- 页面构建离屏回归通过：版本表 8 列，预览/音频/角色页面均可构建并生成截图；当前环境中文字体缺失，仅用于结构检查。
- 主窗口离屏回归被项目目录 ACL 阻断：`logs`/`data` 对当前 `CodexSandboxOffline` 身份不可写，日志可降级但 SQLite WAL 无法初始化；不修改生产代码掩盖该环境问题。
- 重新使用当前沙箱明确可写目录运行 pytest：`................................... [100%]`，35 项全部通过。
- 完成前验证复跑：app 与 tests 共 77 个 Python 文件内存编译通过；`ruff check --no-cache tests app/core app/ui` 通过；35 项 pytest 全部通过；MainWindow 与新增页面壳层导入 smoke 通过。
- 完成前验证仍记录项目目录 `.pytest_cache`/日志写入 ACL 警告；它不影响专用可写目录测试结果，也不改变用户已确认的正常启动结果。
- 用户反馈图片预览空状态出现整条黑色横条；根因是 `empty_label` 被作为普通控件排在图片列表和状态栏之后，占用独立布局行。
- 已将图片预览和角色页空状态改为内容区 `QGridLayout` 叠加层，文字居中显示并设置鼠标穿透；专门截图确认不再形成底部横条。
- 补充 UI smoke 断言，锁定空状态必须挂在 `viewContent` 内容区；Ruff、77 文件内存编译和 35 项测试复跑通过。
- 用户进一步反馈角色页面板间出现默认黑色间隙；根因是 `viewContainer`/`viewContent` 使用透明 QWidget，未覆盖 Qt 默认背景。
- 已在应用级 QSS 为 `viewContainer` 和 `viewContent` 绑定 `BG_DARK`，保留布局间距但消除黑色缝隙；预览/音频/角色三页离屏回归通过，35 项测试复跑通过。
- 用户确认背景间隙修正无问题后，进入角色页详情区优化：未选择时显示中央提示“从左侧选择角色 / 查看详细属性”，详情正文隐藏；选择角色后由现有 `_update_character_detail()` 回调切换显示。
- 刷新角色列表或选择清空时，详情区恢复空状态，避免展示过期角色详情；左侧搜索、选择和数据顺序逻辑保持不变。
- 角色详情离屏截图与 UI smoke 通过，完整 pytest 35 项通过。
- 用户确认角色空状态、选中详情、刷新/搜索切换均正常，并提出角色详情 Wiki 化需求。
- 已审查角色详情数据链路：`character_presenter.py` 当前把身份、属性、技能、消耗、语音和故事拼成单段 HTML；现有解析字段足够支撑第一阶段 Wiki 展示，无需先改 Lua 解析器。
- 已将“角色 Wiki 详情页”纳入 Issue #27 后续子计划，并创建 Issue #28，明确结构化资料模型、Qt 分区组件、协作边界、非目标和验收范围。
- 已完成 `CharacterProfile` 纯 Python 展示模型，复用已有解析字段并补充简介、徽章建议等 Wiki 信息；未修改 Lua、缓存或 CSV 契约。
- 已完成 `CharacterProfileView` 首版：身份头部、简介、核心/战斗属性、技能、成长消耗、徽章建议、故事和语音区块；技能数字支持一键高亮/关闭。
- 已完成角色视图和主窗口接线，角色选择、刷新清空、搜索和详情传递改为使用结构化模型；原有 `build_character_detail_html()` 保留给兼容测试和迁移期使用。
- 已补充角色模型、数字高亮和页面壳层 smoke 测试；针对性测试 6 项通过，项目目录 pytest 缓存仍有既有 ACL 警告，需用明确可写 basetemp 做完整验证。
- 用户已确认角色 Wiki 详情、技能数字高亮和角色页相关交互正常。
- 按协作流程创建 Issue #29：图片预览、音频与版本工作区视觉收口，明确不改导出、解密、下载和数据契约。
- 完成本轮页面改动：预览选择状态反馈、音频内容区空状态、音频播放中文文本、版本表格主题化，以及相关右键菜单 emoji 清理。
- 针对性 Ruff 与页面壳层测试首次发现预览进度条迁移残留 `get_color()` 调用；已按根因移除残留内联样式，复跑通过。
- 已完成 Issue #29 文档同步：`README.md`、`docs/开发文档指南.md`、`开发计划与沟通事项.md`、`docs/worklog/task_plan.md`、`docs/worklog/findings.md` 均记录本轮边界和变更。
- Issue #29 完成前验证：Ruff 通过，80 个 Python 文件内存编译通过，专用可写临时目录下 37 项 pytest 通过；预览、音频、版本页正式蓝灰主题离屏构建截图通过。
- 当前等待用户正常启动验证：预览选择数、音频空状态/播放文本、版本表格样式和既有页面功能回归。
- 用户已确认 Issue #29 页面视觉收口和功能回归均正常。
- 按协作流程创建 Issue #30：对话框与主窗口局部视觉收口，明确不改图片查看、角色选择、导出设置和调试清理的业务契约。
- 完成图片查看器、角色选择、导出设置三类对话框的对象名/主题令牌迁移；图片查看器无图片时导航按钮现在明确禁用。
- 完成图片与音频右键菜单的全局主题化和 emoji 清理；保留原有菜单动作和回调。
- 新增对话框契约测试 3 项；针对性测试共 5 项通过，三类对话框正式蓝灰主题离屏截图通过，等待用户实机验证。
- Issue #30 完成前验证：Ruff 通过，81 个 Python 文件内存编译通过，专用可写临时目录下 40 项 pytest 通过；`git diff --check` 无空白错误。
- 当前等待用户正常启动验证：图片查看器翻页/关闭、角色选择全选/全不选/确认取消、导出设置参数、图片与音频右键菜单。

### Test Results
| Test | Expected | Actual | Status |
|------|----------|--------|--------|
| 初始 Git 状态 | 工作区无未预期改动 | 规划文件刚创建，后续需决定是否纳入版本控制 | in_progress |
| 主题实现审计 | 识别主题入口与主要风险 | 已确认 `theme.py`、`main_window.py` 及多个 views/dialogs 存在全局/局部 QSS 双轨 | pass |
| 信息架构审计 | 识别主要工作区与动作层级 | 已确认顶部/左侧工具栏职责混杂，中央内容区为多视图显隐切换 | pass |
| 离屏视觉基线 | 观察真实窗口的比例、密度和层级 | 已生成 `E:\AI-Agent\xl_ui_baseline_1440.png`；中文字体在离屏环境缺失，已单独标记，不作为产品结论 | pass |
| 功能视图基线 | 观察预览/音频/角色页共同问题 | 已生成三张离屏截图；确认空状态与工具栏存在共性改造机会 | pass |
| 主题对比基线 | 比较 old/new/light 的完整窗口表现 | 蓝灰主题可作为基线；浅色主题存在明显局部样式失配；旧主题不再适合作为默认 | pass |
| 参考 Skill 校验 | 验证 PySide6 桌面 UI 参考资料可用 | 60 个包校验、459 个主题令牌校验和各配置对比度检查通过 | pass |
| 开工协作登记 | 代码改动前创建 Issue | Issue #27 已创建，等待进入第一阶段实现 | pass |
| 第一阶段实现状态 | 只改视觉基础和主窗口壳层 | 已修改 `app/ui/theme.py` 与 `app/ui/main_window.py`，待执行编译、导入和截图验证 | in_progress |
| 第一阶段离屏启动 | 主窗口能在正式主题下创建 | `SCREENSHOT_OK 1440 900 1 new` | pass |
| 全量测试 | 现有测试全部通过 | 专用可写目录运行 pytest 输出 35 个通过断言，退出码 0 | pass |
| 第二阶段静态检查 | 页面壳层改动无语法/规范回归 | 内存编译 59 个文件，Ruff `app/ui tests` 全部通过 | pass |
| 第二阶段页面构建 | 页面工厂和版本工作区可构建 | 版本表 8 列，预览/音频/角色/版本页面截图生成成功 | pass |
| 第二阶段用户验证 | 正常启动和页面切换真实可用 | 等待用户验证本轮页头、操作栏、状态栏和版本摘要 | pending |

### Errors
| Error | Resolution |
|-------|------------|
| 规划模板增量补丁无法匹配 | 使用完整文件重建，没有触碰既有业务文件。 |
| pytest 默认临时目录权限拒绝 | 使用隔离的显式可写 basetemp 重跑，确认是环境问题而非测试/代码失败。 |

## Session: 2026-08-19 — Issue #32 Lua 与角色数据链路

### Current Status
- **Phase:** 6 - Lua 与角色数据链路重构
- **Issue:** [#32](https://github.com/zzzz259/XL/issues/32)
- **Branch:** 待从 `chore/architecture-stabilization` 创建独立功能分支

### Actions Taken
- 已将用户需求拆为 Lua 版本隔离、临时目录清理、最新版本自动解析、Base 文件前置、角色增量数据和未读角标六个子问题。
- 已读取并启用本轮需要的 `collab-workflow`、`planning-with-files` 和 `verification-before-completion` 规范。
- 已确认正在审阅的 UI PR #31 不应混入本轮功能，后续从当前提交创建独立分支。
- 已确认 Issue #32 已创建，范围包含背景、目标、边界和验收标准。
- 已确认当前导出线程在 staging 中反编译 Lua，提交后才同步到单一 `output/lua`；角色解析仍依赖单一目录和单一完整缓存文件。
- 已确认本轮只清理 `data/material/assets/lua`，不删除整个 `data/material`。
- 已完成 `app/core/lua_repository.py`：Lua 版本目录、最新版本、Base 文件前置判断、`.lua` 成品发布和临时目录清理。
- 已完成 `app/core/character_repository.py`：角色版本快照、当前数据合并、new/changed 比较、未读集合和查看后清除。
- 已将 `ImportASWorker` 接入版本时间戳：Lua 成功提交后发布到 `output/lua/<版本>/`，不再合并覆盖单一 `output/lua`。
- 已将 `MainWindow` 接入最新版本/Base 门槛；非最新版本或缺 Base 时只留存 Lua，不自动解析角色；最新有效版本导出完成后自动解析。
- 已在角色列表增加状态列显示“新”，选中角色详情时清除持久化未读标记；历史未查看角色继续保留角标。
- 已同步 `README.md`、`docs/开发文档指南.md` 和 `开发计划与沟通事项.md`，记录运行目录契约、调用链和本轮边界；开发过程记录统一归档到 `docs/worklog/`。

### Errors
| Error | Attempt | Resolution |
|---|---:|---|
| GitHub Issue 请求体内嵌中文引号导致 PowerShell ParserError | 1 | 改用不含嵌套引号的等价文案后成功创建 Issue #32；未产生重复 Issue。 |
| 导入完成提示条件表达式出现双反斜杠语法错误 | 1 | 改用括号包裹的条件表达式；随后重新执行 Ruff、编译、导入 smoke 和全量测试。 |

### Verification
- Ruff：`python -m ruff check --no-cache app/core app/ui tests` 通过。
- 测试：`python -m pytest -q --basetemp E:\\AI-Agent\\xl-pytest-basetemp-lua-full`，46 项通过。
- 最终门禁复跑：85 个 Python 文件内存编译通过，46 项测试通过，Lua/角色模块导入 smoke 通过，`git diff --check` 通过。
- 额外导出链路测试：版本隔离、临时清理、旧版本保留、Base 缺失门槛和角色未读状态均通过。
- 尚未进行用户实机导出验证；下一步需用真实版本执行一次最新 Lua、一次旧版本 Lua、一次缺 Base/无角色 Base 的导出，并确认角色界面自动更新及角标清除。

## Session: 2026-08-19 — Issue #32 回归修复

### Root Cause Investigation

- 已确认角色页切换时的现场解析来自 `_start_lua_decrypt()` 直接调用 `_load_character_data()`；旧版平铺 `output/lua` 还会使版本标识为 `None`，绕过版本仓库缓存。
- 已确认“最新版本”原先取版本数据库最大值，已改为只看 `output/lua/<版本>/` 的实际发布目录。
- 已确认旧 `characters_full.json` 没有成为版本仓库首次合并的基线，导致迁移/重复导出时全量“新”；已加入旧缓存基线和规范化快照比较。
- 已确认 `assets_map.json` 可在当前样本中把 12,797 个 AB 精确筛为 2 个 Lua bundle；仅筛进度列表还不够，AssetStudio 仍会扫描原目录，因此补充了只含命中包的 CLI 临时输入目录。

### Implemented

- 角色页切换现在只恢复角色仓库或本地缓存，不主动解析 Lua；“开始解析”和 Lua 导出后的自动解析仍是显式入口。
- 新增“全部标为已读”，未读红点同步显示在版本列表、图片预览、音频和角色四个顶部标签。
- 自动角色解析沿用导入进度弹窗，导入完成后在弹窗内继续展示解析进度。
- Lua 单分类导入读取版本 `assets_map.json`，只将命中的 AB 放入临时 AssetStudio 输入目录；映射缺失时保留兼容回退并记录警告。
- 新增 `bundle_selector`、仓库迁移基线、全部已读、CLI 隔离目录测试。

### Verification

- 针对性 Ruff 通过。
- 角色仓库、缓存、Lua bundle 筛选、导入线程和页面壳层针对性测试通过（17 项）。
- 实际读取当前样本映射：12,797 个 bundle → 2 个 Lua bundle，命中 13,880 条 Lua 资源。
- 全量 Ruff 通过；内存编译 `app + tests` 共 87 个 Python 文件通过；全量 pytest 通过（51 项）。
- 导入 smoke 通过；当前沙箱对项目 `logs/` 和 `.pytest_cache/` 仍有 ACL 警告，不影响专用临时目录测试结果。
- 尚待完成：用户真实启动与导出验证。

### 启动回归修复

- 用户实机启动日志发现顶部导航重构把图标名称字符串直接传入 `QAbstractButton.setIcon()`，导致正常模式启动即失败。
- 已修正为先通过 `_icon()` 构造 `QIcon` 再传给 `_tbtn()`，新增真实 Qt 工具栏构建 smoke。
- 修复后 Ruff、87 个 Python 文件内存编译、全量 pytest（52 项）均通过；当前沙箱完整主窗口仍受项目 SQLite WAL 写权限限制，用户环境需再次正常启动确认。

### 未读角标语义修正

- 角色未读状态现在只显示在“角色”顶部标签；版本列表、图片预览和音频不再复用角色未读红点。
- 已补充 Qt 回归测试，验证四个导航角标中仅角色角标可见。
- 角标语义修正后的全量 pytest 已通过（53 项）。
