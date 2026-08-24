# Task Plan: XL 界面视觉重设计

## Goal
在不破坏现有功能、数据契约和多人协作边界的前提下，重新审视 XL 的桌面应用视觉系统：保留可用的新蓝灰主题，去除明显的 AI 生成感，建立统一、可维护、可渐进落地的主题、布局、组件和交互视觉规范，并分阶段完成界面优化。

## Next Step
维护 Issue #32 及后续 XL 开发任务的计划、发现和验证记录；当前 Issue #32 已提交 PR #33。

## Current Phase
Phase 6 - Lua 与角色数据链路重构

## Phases

### Phase 1: Requirements & Discovery
- [x] Understand user intent
- [ ] Read existing UI and theme implementation
- [ ] Inventory all top-level pages, dialogs, shared widgets, and theme entry points
- [ ] Document visual problems, constraints, and non-goals in `docs/worklog/findings.md`
- [x] Confirm design direction with user through focused questions
- [x] Evaluate PySide6/Qt-specific visual references and skills
- **Status:** complete

### Phase 2: Planning & Structure
- [x] Establish visual direction and design tokens
- [x] Decide theme retention/removal/migration strategy
- [x] Define first implementation slice and acceptance checklist
- [x] Create GitHub Issue before code changes（#27）
- **Status:** complete

### Phase 3: Implementation
- [x] Implement the approved visual foundation（Issue #27 第一阶段初版）
- [x] Migrate pages incrementally without changing business behavior（当前：统一页面骨架第一轮）
- [x] Add or update focused UI tests/smoke checks（页面构建与离屏截图）
- [x] 修复图片/角色空状态从底部横条改为内容区中央叠加
- [x] 优化角色详情区空状态与列表密度（当前切片实现完成，待用户验证）
- [x] 角色 Wiki 详情页：结构化数据模型与 Qt 原生展示组件（Issue #28 首版）
- [x] 角色 Wiki 详情页：用户实机验证与长文本/多角色回归
- [x] Issue #29：图片预览、音频、版本工作区主要局部 QSS 收口
- [x] Issue #29：用户实机验证与页面交互回归（用户已验证）
- [x] Issue #30：对话框与主窗口局部视觉收口
- [x] Issue #30：用户实机验证与对话框操作回归（用户已验证）
- **Status:** in_progress

### Phase 4: Testing & Verification
- [x] Run compile/import/lint checks
- [x] Launch the application and inspect screenshots/interactions（用户已确认第一阶段）
- [x] Launch and inspect the second-stage page shell（用户已验证）
- [x] 验证角色详情区空状态与选中后的详情切换（用户已验证）
- [x] 验证 Wiki 详情分区与技能数字高亮（用户已验证）
- [x] 验证 Issue #29 的图片选择反馈、音频空状态/播放文本、版本工作区样式（用户已验证）
- [x] 验证 Issue #30 的图片查看器、角色选择、导出设置和右键菜单（用户已验证）
- [x] Validate theme compatibility and major workflow regression（35 项测试 + 页面构建）
- [x] Record fresh verification evidence
- **Status:** pending

### Phase 5: Delivery
- [x] Update required project documentation（已同步角色 Wiki 首版结构与边界）
- [x] Review diff for scope creep and collaboration conflicts（PR #31 已创建）
- [x] Report results and wait for user confirmation before PR（用户已确认并已创建 PR #31）
- **Status:** complete

## Phase 6: Lua 导出与角色数据链路（Issue #32）

### 目标

将 Lua 导出从临时 `data/material/assets/lua` 改为按版本留存的最终产物，并在满足“最新版本 + 必要 Base 文件”条件时自动完成角色数据增量同步。保留手动解析按钮作为补偿入口，不改变 Lua 解析字段和 CSV 导出契约。

### 阶段

- [x] 建立 Issue #32，明确范围、边界和验收标准
- [x] 审查版本标识、导出线程提交点、角色解析入口和现有缓存格式
- [x] 设计并实现 `output/lua/<版本>/` 版本目录与临时 Lua 清理
- [x] 实现最新版本/Base 文件前置判断和导出后自动解析
- [x] 将角色数据改为按版本增量合并，保留历史快照与新/变更状态
- [x] 在角色界面显示新角标并实现查看后清除
- [x] 补充测试、文档和本轮报告
- [ ] 用户实机验证：切换角色页不解析、最新已发布 Lua 自动解析、旧版本不触发、缺 Base 不触发、角标与全部已读
- [ ] 用户实机验证：仅勾选 Lua 时只导入映射命中的 AB

### 本轮边界

- 本轮只处理 Lua 导出和角色数据；音频 debank、FGUI、Spine 立绘合并留待后续批次。
- 暂不删除整个 `data/material`，只在 Lua 成功提交后清理 `data/material/assets/lua`。
- 不破坏导出失败/取消时的已有最终产物，不把历史版本 Lua 覆盖为当前版本。

## Character Wiki Detail Plan（Issue #27 后续子计划）

### 目标
将当前“完整数据拼成一段 HTML 文本”的角色详情，改为可扫描、可滚动、可扩展的轻量 Wiki 资料页。优先复用已有解析字段，不改变角色数据来源、缓存和 CSV 导出。

### 分阶段实现

1. **结构化展示模型**
   - 在 `app/core/` 增加纯 Python 的角色资料展示模型/构建器，按身份、属性、技能、成长、语音、故事和徽章推荐分组。
   - 保留 `build_character_detail_html()` 作为兼容入口或迁移过渡，不让 Qt 控件继续承担字段拼接。
   - 为缺失字段、未知值、多行文本和超长技能描述定义统一显示策略。

2. **Wiki 详情组件**
   - 在 `app/ui/widgets/` 拆出角色资料组件，不把所有布局继续堆回 `character_view.py`。
   - 身份头部：名称、ID、星级、职业、属性、阵营、CV 等标签。
   - 核心属性：生命/攻击/防御的初始→满级展示，暴击/格挡/速度/范围等次级属性网格。
   - 技能区域：队长、普通、特殊、爆发、被动、觉醒分区卡片，长文本可读且可滚动；提供数字一键高亮，便于快速核对技能倍率和持续时间。
   - 成长与消耗：突破、技能升级消耗表格化。
   - 语音与故事：折叠或分组展示，避免 30 多条语音挤压核心资料。

3. **内容美术与交互**
   - 统一使用蓝灰语义色、标题层级、分区间距和状态标签；减少无意义边框，不做卡片堆叠。
   - 详情区保持键盘可滚动、长文本不截断、选择角色后滚动位置可控。
   - 角色立绘/头像只在已有文件路径可可靠映射时接入，不用临时生成图或不确定的资源猜测。

4. **验证与协作边界**
   - 保持 `MainWindow` 只负责选中角色和传递资料，数据分组与组件布局分别放在 core/widgets。
   - 增加结构化展示模型测试、缺失字段测试、长文本/多行文本测试和 UI smoke。
   - 人工验证至少覆盖 101 个角色、角色切换、搜索后选择、刷新后无旧详情、长技能文本和 CSV 导出不受影响。

### 非目标

- 本子计划不重写角色 Lua 解析器，不改变缓存格式，不修改 CSV 字段契约。
- 第一阶段不强行接入角色图片、装备系统、关系图谱或复杂动画；先把资料层级和阅读体验做好。

## 既有页面后续工作（不丢失）

- **图片预览**：继续检查筛选/多选/导出参数/合成图工作流的操作栏密度、缩略图空白比例和大图查看器层级；不改导出契约。
- **音频页面**：继续检查分类树、播放栏、播放状态和批量导出反馈，统一与角色 Wiki 相同的页面节奏；不改音频扫描与解密逻辑。
- **版本工作区**：继续检查版本摘要、下载状态、版本选择和备注 delta 的信息优先级，避免摘要与下载动作争夺视觉焦点。
- **全局主题与协作**：角色详情验证后再做跨页面 token 收口、局部 QSS 清理和多人协作文件边界复查；每一轮保持小 Issue、小 diff 和独立测试。

## Issue #29 本轮收口内容

- 图片预览：迁移缩略图列表和加载进度条的样式到主题令牌；底部状态增加“共 N 张图片 · 已选 M”，筛选后同步更新。
- 音频页面：增加内容区空状态；清空/不存在目录时重置叶节点列表，避免旧数据残留；播放按钮和当前播放文本改为稳定中文文本，去除 emoji。
- 版本工作区：版本表格样式迁移到 `workspaceTable` 主题选择器，减少页面内联 QSS。
- 共享页面：去除图片/音频右键菜单中的 emoji 文本，避免字体缺失和视觉风格分叉。

## Issue #30 本轮收口内容

- 图片查看器：统一顶部文件名栏、底部导航栏、关闭按钮和画布背景；无图片时禁用上一张/下一张。
- 角色选择：统一标题、全选/全不选、滚动列表和确认/取消按钮；空角色列表显示明确提示。
- 导出设置：移除对话框内联 QSS，使用统一标题、字段标签、文件路径和主次按钮语义。
- 主窗口菜单：图片/音频右键菜单迁移到全局菜单样式，清理 emoji 文本并保留原动作回调。

## Decisions Made
| Decision | Rationale |
|----------|-----------|
| 先审计再实现 | 视觉重设计涉及全局主题、页面层级和共享组件，必须避免凭感觉局部改色。 |
| 保留新蓝灰主题作为基线 | 用户确认它目前相对可用，但需要降低 AI 感并统一细节。 |
| 暂不使用生成图替代 UI | XL 是桌面工具，优先改进信息层级、排版、间距、状态和交互反馈；素材生成只在确有价值时引入。 |
| 继续沿用 collab-workflow | 新技能负责澄清、记忆、调试和验收，不替换 XL 的 Issue/PR 主流程。 |
| 页面壳层集中到 `widgets/view_chrome.py` | 统一页头、操作栏、状态和空状态，降低各页面重复 QSS 与多人协作冲突。 |
| 版本列表采用工作区摘要而非装饰卡片 | 用标题、说明和轻量统计改善信息层级，避免继续堆叠大面积边框卡片。 |
| 空状态使用内容区叠加 | 空状态是上下文提示，不应占用底部布局高度；叠加后可保持图片区/详情区的主体比例。 |

## 2026-08-21 音频导出流程（Issue #35）

- [completed] 创建中文 Issue #35，并从最新 `origin/main` 建立音频专用分支。
- [completed] 审计真实 AB、Lua、音频 output 和旧 material 中间文件，确认 event_33/event_34 属于第五专辑。
- [completed] 实现自动音频后处理、临时目录清理、增量状态、专辑分类和精准 AB 筛选。
- [in_progress] 完成 UI 单选互斥、红点验证、全量测试和用户实机导出验证。

### 本轮边界

- 包含：音频导出后自动解析、fmodassets 临时清理、精准 AB 筛选、专辑分类纠正、增量音频状态、单行互斥选择。
- 不包含：Lua 版本隔离、FGUI 图集和 Spine 合并。

## Errors Encountered
| Error | Resolution |
|-------|------------|
| 初次增量补丁无法匹配脚本生成的模板行 | 改用完整文件重建，保留规划文件用途与编码。 |

## 2026-08-21 音频中日语音同名修复

- [completed] 根据实机日志确认中文 bank 已进入解密流程，根因是旧分类清理按文件名全局扫描。
- [completed] 先添加回归测试并确认修复前失败，再按 `voice/<bank>/<cn|jp>` 作用域隔离清理。
- [completed] 保留 BGM 跨专辑旧分类清理，避免影响既有专辑纠正逻辑。
- [in_progress] 等待用户重新执行真实中日语音导出验证；`assets_map.json` 缺失导致的全量 bundle 回退另行处理。

## 2026-08-21 全量音频审计与未读树标记

- [completed] 核对 AS、debank、最终文件数量和日文文件名，确认没有字符编码失败，但发现 bank 级静默空产出风险。
- [completed] 对照 BaseSound/BaseSoundChapter 和输出专辑，发现旅途轶事缺 E20，未分类 10 个为配置表之外的额外 BGM。
- [completed] 将“新”标记从叶节点向上同步到所有父级文件夹。
- [completed] 后续单独处理 E20、064/095/080 bank 错位和 debank 并行化，不与本轮 UI 标记改动混合。

## 2026-08-21 音频定向修复结果

- [completed] 将 QuickBMS/FSB 解包从共享 `_tempo/<bank>` + `result/` 改为每 bank 唯一 job 目录。
- [completed] 加入默认 2 路受控并行，并保持最终 output/audio 写入串行。
- [completed] 将 bank 空产出、QuickBMS/FSB 失败和复制失败纳入结构化统计与日志。
- [completed] 清理 064/095 的旧异号语音残留；保守归一化 080 的连续重复事件后缀。
- [completed] 完成针对性回归、全量测试、Ruff、内存编译和 diff 检查。
- [in_progress] 等待用户真实全量导出，确认最终产物和性能收益；并行度可根据实机耗时再调优。

## 2026-08-21 音频定向修复第二轮收口

- [completed] 复现 E20 bank：确认 QuickBMS 返回成功但 FSB 子进程因日文 event 名称异常退出，存在“外层成功、内层失败”的静默空产出风险。
- [completed] 保留旧版 `fsb_aud_extr` 主路径，增加受控超时与 vgmstream fallback；通过 `.extract_status.json` 将 FSB 子进程失败传递给 QuickBMS 调用层。
- [completed] 复现 JP 064/095：确认错位发生在 FSB 内部 event 名称，而非 AssetStudio 临时文件覆盖或并行目录串写。
- [completed] 对单一异号前缀执行保守归一化；混合前缀和目标文件冲突时保持原样，避免误改正常音频。
- [completed] 真实验证 E20 单 bank fallback、旧版 FSB 成功路径，以及 064/095 两 bank 完整导出管线。
- [in_progress] 执行最终全量测试、静态检查和 diff 审查；保留用户实机全量导出作为提交 PR 前的最后验收门槛。

## 2026-08-22 音频全量导出复查（待确认范围）

- [in_progress] 调查全量导出后“新”状态无法消除、BGM 专辑产物缺失、取消动作不生效/队列继续执行和整体速度慢四个问题。
- [pending] 对照当天日志、`output/audio`、Lua 专辑映射和 bank 级统计，确认每个问题的实际根因与相互关系。
- [pending] 由用户确认修复边界后，再拆分为可独立提交的小 commit，并补充回归测试与全量导出验收方案。

### 2026-08-23 第三轮：debank 性能实测与直解方案

- [completed] 读取用户提供的优化建议，并逐项对照当前 QuickBMS、FSB 提取、临时目录、复制和缓存实现。
- [completed] 从本地完整 AB 版本建立独立 staging，验证 AssetStudio 能导出完整 FMOD `.bytes`，不污染项目现有产物。
- [completed] 完成同批 12 bank 的旧链路/直解对照和 735 bank 直解全量基准，确认直解优先具备明确收益，SFX 失败需要旧链路兜底。
- [completed] 修改 bank/FSB 解码为 vgmstream 直解优先，失败后回退 QuickBMS/旧 FSB 提取器；补充日志中的解码方式和 bank 级统计。
- [completed] 根据 2/4/6/8 路样本结果将默认 worker 设为 4；同盘成品优先原子移动，跨盘回退复制。
- [completed] 更新 README、开发指南、版本历史、根目录计划和工作记录；pytest 77 passed、Ruff、compileall、diff 检查全部通过。
- [pending] 用户侧再执行一次正式音频导出，确认真实机器上的输出数量、BGM 专辑归类和 23 个 SFX 空产出是否符合预期。

### 当前只读证据

- 最新日志：`logs/app_20260822_140730_100108.log`，约 3.34 MB。
- 当前音频产物：7,118 个音频文件，约 4.65 GB；`output/audio/album` 最近修改时间早于 `voice`，需继续核对是否本轮 BGM 没有进入复制阶段。
- 本轮先不修改导出逻辑；用户提供的速度建议作为候选方案输入，不视为已验证事实。
### 2026-08-22 音频全量导出复查（调查阶段）

- [x] 读取最新日志、音频状态文件和 `output/audio` 产物快照。
- [x] 定位专辑 BGM 全量缺失的复制阶段根因。
- [x] 定位取消后旧任务继续运行的线程/子进程生命周期问题。
- [x] 确认重复全量解密的当前性能瓶颈，并审阅 vgmstream/缓存/零拷贝建议的适用边界。
- [ ] 确认“新”标记的实际复现路径、取消语义和首轮性能/格式边界。
- [x] 设计并实施最小根因修复，补充回归测试和小范围真实验证。
- [x] 加入 bank 指纹缓存，并保持 WAV 输出和 vgmstream fallback 边界不变。
- [ ] 执行小范围真实音频链路验证，确认 BGM、取消和缓存日志符合实机行为。

#### 当前验证边界

- 仓库内没有可复用的 `.bank/.fsb` 实体素材，当前项目的 `data/material/assets/fmodassets/` 也已被上一轮流程清空。
- 已完成合成临时目录、进程取消和缓存失效的真实子流程测试；BGM 最终成品和实机缓存命中需等待下一次音频导入后验收。

#### 调查错误

| 错误 | 原因 | 处理 |
|---|---|---|
| 读取 `_subcontractors/_epic7_defsb.py` 失败 | 实际入口位于 `tools/epic7_debank_v1_0/_epic7_defsb.py`，不在 `_subcontractors/` | 已改用仓库文件清单定位真实入口，继续只读调查 |
| 更新 `progress.md` 首次补丁未匹配 | 目标上下文文字与文件尾部实际内容不一致 | 重新读取文件尾部后改用精确上下文追加，已完成记录 |
| `compileall` 写入仓库 `__pycache__` 被拒绝 | 仓库已有缓存目录 ACL 不允许当前账户覆盖临时 `.pyc` | 将下一次编译缓存重定向到 Codex 工作目录 `tmp`，不修改仓库权限 |
| 全量 pytest 固定 basetemp 无法清理、Ruff 写 `.ruff_cache` 被拒绝 | 仓库/旧临时目录 ACL 限制，验证工具默认写入受限位置 | 改用新的 Codex 临时目录，并设置 `RUFF_CACHE_DIR` 到 Codex 工作目录后重跑 |
| 全量 pytest 暴露旧测试 fake `epic7_debank.run()` 不接受 `use_cache` | 生产调用新增了缓存控制参数，测试替身接口未同步 | 补齐测试替身参数后重跑全量门禁 |

### 2026-08-23 用户实机反馈修复计划

- [completed] 对照实机日志、Lua 音频配置和 output 产物，确认解码慢、缺失与未分类的真实边界。
- [completed] 修复 bank 性能、列表重复加载、未读聚合、播放 seek 和树形层级选择。
- [completed] 补充回归测试、静态检查、文档同步和本地检查点提交。

## 2026-08-23 日志与 Debug 诊断收口

### 目标

在不改动 UI 布局和业务导出契约的前提下，让普通启动与 Debug 启动拥有真正不同的运行配置，并把关键操作、任务链路、组件阶段和外部进程结果记录成可追踪的诊断日志。

### 本轮范围

- [completed] 审计 `logger.py`、`main.py`、路径配置、Worker 生命周期和外部进程调用点，确认当前日志配置与 Debug 参数的实际传播路径。
- [completed] 建立启动前解析参数、运行模式配置和日志初始化顺序；普通模式保持精简，Debug 模式开启详细诊断。
- [completed] 引入 session/task/component/stage 上下文，覆盖导入、Lua、音频和外部工具主链路。
- [completed] 收口 AssetStudio manifest 和 Spine CLI 外部进程日志，记录工具、参数摘要、退出码、耗时和失败时 stdout/stderr 尾部；普通日志不接收 DEBUG 细节。
- [completed] 增加启动环境摘要、未捕获异常与线程异常的诊断记录；UI 布局标识、Debug 数据目录隔离和失败现场保留列为后续计划，不在本轮实现。
- [completed] 补充日志配置、任务上下文、外部进程和异常报告测试，并同步 README、开发指南、版本历史和工作记录。
- [pending] 用户启动验证：分别用 `run.bat` 和 `debug.bat` 启动，检查日志会话目录、日志级别、环境摘要和异常报告路径。

### 验收标准

1. `--debug` 在导入任何应用模块前参与运行时配置和日志初始化。
2. 普通模式与 Debug 模式的控制台、文件日志级别和外部进程输出策略可区分，并有测试覆盖。
3. 一次大操作可以通过 task_id 追踪到主要子阶段；内部子任务可记录 parent task。
4. 关键缓存、筛选、跳过、失败和外部进程决策能从 Debug 日志解释原因。
5. 日志改造不改变现有导出结果、取消语义和 UI 布局。

## 2026-08-23 项目结构与多人协作架构审查

### 目标

从长期维护和多人并行开发出发，识别公共文件热点、职责耦合、依赖倒置、测试边界和数据/运行时契约问题，形成不影响现有建设的分阶段结构调整计划。本阶段只评估、记录和规划，不实施目录迁移。

### 审查阶段

- [completed] 盘点源码、测试、运行目录、外部工具入口和现有文档契约。
- [completed] 用提交历史统计公共文件热点，区分“高频但合理的协调文件”和“应拆分的职责耦合”。
- [completed] 构建 import 依赖和调用链地图，确认 core/ui/workers/features/adapters 的实际边界。
- [completed] 评估测试分层、共享状态、配置/路径/日志入口和多人协作冲突面。
- [completed] 输出目标结构、迁移顺序、每阶段验收标准、回滚策略和不应触碰的兼容契约。
- [completed] 将审查结论同步到架构基线、开发计划和工作记录。
- [pending] 等用户确认计划后创建结构调整 Issue；确认前不修改源代码、不移动目录、不改变运行时契约。

### 暂定原则

- 先抽“应用服务/用例编排”和“领域纯逻辑”，再移动目录；不以换文件夹作为重构目标。
- `main_window.py` 最终只保留窗口生命周期、导航和信号绑定；业务流程通过应用服务/控制器注入。
- Worker 只负责线程生命周期、进度/取消信号和异常转译；文件扫描、外部工具和提交事务下沉到可测试服务。
- 以领域边界拆分协作 ownership：版本/下载、AS 导入、Lua/角色、音频、立绘/Spine、FGUI、诊断运行时分别拥有稳定入口。
- 先保持 output、data、logs、数据库和 Qt 信号契约，迁移完成并有回归证据后再考虑目录或数据格式变更。

### 评估结论

- [completed] 完成源码规模、提交历史、调用依赖、外部工具入口、测试和协作文件审查。
- [completed] 确认主要公共热点是 `main_window.py`，并确认 `core`、Worker、`features` 和旧 AssetBrowser 之间仍存在职责交叉。
- [completed] 确认当前没有 CODEOWNERS/目录 ownership 门禁，架构测试也尚未覆盖依赖方向和公共文件白名单。
- [completed] 形成“应用服务 + 领域逻辑 + 基础设施 + Qt 壳层”的渐进式目标结构；不采用一次性目录大迁移。

### 建议实施路线（等待用户确认后执行）

#### P0：协作边界与架构门禁

- 新增领域 ownership 表和公共文件变更规则，明确版本/下载、AS 导入、Lua/角色、音频、立绘/FGUI、诊断运行时的负责范围。
- 将 `main_window.py` 标记为迁移中的壳层文件：新功能不得继续把领域逻辑写入其中；必须有对应 Issue 和迁移目标。
- 增加架构测试/静态检查：应用层不得依赖 Qt，领域层不得依赖 UI，主窗口依赖采用白名单，外部工具调用逐步收口到 infrastructure adapter。
- 保持此阶段无行为变化，先建立检查和 ownership 规则，避免边拆边失控。

#### P1：先抽应用服务，冻结主窗口增长

- 先抽“版本工作区/下载”用例，建立 `application` 入口和结果对象；`MainWindow` 只负责收集选择、启动任务、接收结果并更新视图。
- 再抽 AS 导入总流程，将选择 AB 包、分类计划、临时目录、提交结果和后续 Lua/音频触发从窗口方法中移出。
- 为每个应用服务提供无 Qt 的测试，保留现有 Worker 信号和 output/data/logs 契约，旧调用先通过兼容入口转发。

#### P2：Worker 薄化与外部工具收口

- 将 ImportAS、AudioDecrypt、LuaDecrypt、PreviewExport 的文件处理、外部进程、取消传播和提交事务下沉到可测试服务/adapter。
- Worker 只保留线程生命周期、进度/取消/完成/失败信号和异常翻译；不得继续成为领域流程的唯一实现位置。
- 统一 AssetStudio、vgmstream/QuickBMS、Spine 等外部工具的命令、超时、输出和失败结果协议；旧 AssetBrowser 只做兼容，不再扩展新功能。

#### P3：按领域拆分 core 与 UI ownership

- 在调用方切换完成后，再将现有 `core` 按 `version`、`import_pipeline`、`lua_character`、`audio`、`preview`、`diagnostics` 等领域分包；路径、数据库、日志、文件事务和外部工具进入 infrastructure。
- 将页面控制逻辑从 `main_window.py` 分到版本、导入、音频、角色、预览控制器；视图只构造控件和展示状态。
- `character_loader.py`、`theme.py` 等大文件按稳定职责拆分，先保证纯函数和现有公共导入兼容，再删除旧实现。

#### P4：协作发布与清理

- 增加 `CODEOWNERS` 或等价的目录审查规则，按领域控制 PR 范围；主窗口和跨领域契约变更需要额外审查。
- 将测试按 `tests/domain`、`tests/application`、`tests/infrastructure`、`tests/ui` 分层，保留现有测试作为迁移期兼容集，逐步补齐契约测试。
- 旧入口和兼容 re-export 至少保留一个稳定周期；确认调用方、测试和文档全部切换后，再删除 `asset_browser.py` 或旧模块别名。

### 每阶段不变契约

- 不改变 `output/` 最终产物结构、`data/` 数据库/Bundle 位置、`logs/<session_id>/` 日志结构、Lua/音频/立绘导出结果和取消语义。
- 每个阶段只解决一个边界，使用小型本地检查点提交；阶段完成后统一运行 pytest、Ruff、Python 编译、diff 检查和必要的 Windows 实机 smoke，再合并为最终 PR。
- 任何需要修改 `main_window.py` 的迁移必须与功能需求分离；迁移 PR 只改变依赖和职责，不顺手加入 UI 或业务新特性。

## 2026-08-23 第三方所有权审查复核后的完整实施计划

> 本节的 P0-P8 是当前执行基线；前文较早的 P0-P4 仅保留为历史路线记录，不与本计划并行执行。

### 总体决策

- [completed] 接受第三方报告对“横向技术分层尚未形成代码所有权分层”的核心判断。
- [completed] 确认 `main.py` 不是主要问题，保留轻量启动入口；主要改造对象是 `MainWindow`、页面工厂、Worker、`core` 和共享文档。
- [completed] 采用“功能域优先、域内分层”的目标结构，但不进行一次性全仓搬迁。
- [completed] 将第三方报告的 17 个要点逐条复核并写入 [ownership-review-2026-08-23.md](ownership-review-2026-08-23.md)。
- [in_progress] Issue #39 已创建，开始执行 P0 架构契约与 ownership 基线；本阶段不迁移业务实现。

### 目标结构与过渡策略

目标结构为 `bootstrap / shell / features / platform / shared`。过渡期保留 `app/core` 和 `app/ui`，采用新入口转发旧实现的方式迁移调用方，禁止为了目录整洁进行大规模重命名。

功能域 ownership：

| 功能域 | 目标目录 | 主要负责人范围 | 普通功能 PR 是否允许改 Shell |
|---|---|---|---|
| 音频 | `app/features/audio/` | 页面、服务、仓库、分类、审计、debank | 否 |
| 角色 | `app/features/characters/` | 页面、服务、仓库、Lua parser | 否 |
| 预览 | `app/features/preview/` | 图片、FGUI、Spine、视频 | 否 |
| 版本 | `app/features/versions/` | 版本表、更新、下载、Bundle 状态 | 否 |
| 导入 | `app/features/importer/` | Bundle、AssetStudio、导出计划、发布事务 | 仅装配协议变更时 |
| 公共 UI | `app/shared/qt/` | 主题、页头、命令栏、空状态、通用控件 | 需额外审查 |
| 平台与诊断 | `app/platform/` | 路径、文件、数据库、进程、日志、Debug | 需额外审查 |
| 壳层与装配 | `app/shell/`、`app/bootstrap/` | 导航、任务宿主、Feature 注册、运行时组装 | 属于公共架构区 |

### 阶段 0：架构契约与协作门禁

当前状态：completed（Issue #39；用户已完成正常启动验证）

目标：只建边界，不改变业务行为。

实施内容：

- 建立 `Feature`、`Page`、`Controller`、`Service`、`Worker` 和 `ImportResult` 的最小协议。
- 增加 `app/bootstrap/app_factory.py` 草图和 `AppContext`，先允许旧模块作为实现注入。
- 增加 AST 架构测试：禁止 Shell 导入领域实现；禁止 Page 使用 `parent._xxx`；禁止新代码从 Shell 访问 `database`、解析器和外部工具；禁止应用服务依赖 Qt。
- 增加 ownership 清单；根据协作者确认结果再落地 `CODEOWNERS`。
- 建立每个 Feature 的变更范围、测试入口、输出契约和回滚要求。

验收：所有现有测试通过；应用启动方式、日志、output/data 目录和导出结果不变；架构测试只约束新增边界，不误伤旧兼容代码。

### 本次执行边界

- 本次只新增无 Qt 的契约、组合根草图、架构测试和 ownership 文档。
- 不修改 `app/ui/main_window.py`、`app/core/`、任何 Worker、导出逻辑或数据格式。
- P0 完成并经用户启动验证后，再进入 Audio Feature 迁移。

### 阶段 1 当前执行边界

- Issue #40 已创建，开始 P1 Audio Feature ownership 模板迁移。
- P1a 已完成音频 Page、Qt-free AudioService、树逻辑 Feature 入口和旧模块兼容转发。
- P1b 已完成播放器/选择状态、AudioController、AudioDecryptWorker 功能域入口和任务结果回调迁移；MainWindow 不再持有音频 Worker、播放器或音频控件状态。
- 每个子步骤保持旧入口可回退；未经用户验证，不删除旧音频实现。

### P1a 完成记录：Audio Page 与目录服务

- `app/features/audio/page.py` 新增 `AudioPage`，页面自持控件，只发出关闭、刷新、导出、播放、选择、进度和音量等语义信号。
- `app/features/audio/service.py` 新增 `AudioService`，集中处理 `output/audio/` 扫描缓存、快照未读状态和选中导出；不依赖 Qt。
- `app/features/audio/tree.py` 接管音频树构造、目录递归勾选和未读标记聚合；`app/ui/features/audio_controller.py` 保留兼容转发。
- `MainWindow` 已切换到 `AudioPage` 和 `AudioService`；导出、播放、取消、分类、未读和输出目录契约保持不变。
- 新增 `tests/test_audio_feature.py` 及架构边界断言；P1a 不删除旧 `audio_view.py`，为 P1b 回退保留入口。

### P1b 完成记录：AudioController 与任务适配

- `app/features/audio/controller.py` 接管音频列表缓存、目录/文件勾选、播放进度、音量、右键菜单、导出和未读状态。
- `app/features/audio/worker.py` 提供功能域侧 `AudioDecryptWorker` 入口，当前包装迁移期 Worker，保持 bank/debank、取消和进度信号契约不变。
- MainWindow 只保留音频页面显隐、导入流程总协调和结果提示；音频页面信号由 AudioController 自己消费。
- 导入共享进度弹窗通过 `processing_finished/processing_cancelled/processing_error` 结果信号回到 Shell；独立进度弹窗和 Worker 生命周期由 Controller 管理。
- 旧 `audio_view.py`、`ui/features/audio_controller.py` 和 `ui/workers/audio_decrypt.py` 暂保留兼容入口，后续确认调用方全部切换后再删除。
- P1 代码门禁：全量 pytest 100 项通过，Ruff、AST 解析 111 个 Python 文件和 `git diff --check` 通过。

### 阶段 2 当前执行边界

- Issue #41 已创建，开始 Characters Feature ownership 迁移。
- 本轮先迁移 CharacterPage、角色列表/筛选/详情控制器和本地数据恢复服务；Lua parser、仓库 JSON 契约和 Wiki 展示模型保持兼容。
- 角色解析入口、Lua 导出后的自动解析、全部标为已读、打开详情清除未读和 CSV 导出均保持行为不变。
- 未经用户验证不删除 `app/ui/views/character_view.py`、`app/core/character_*` 或 MainWindow 的兼容入口。

### Errors Encountered

| Error | Attempt | Resolution |
|---|---:|---|
| 沙箱内 `gh auth status` 网络访问被拒绝，普通凭据检查未发现可用 Credential | 1 | 使用已获授权的 `gh` 网络调用重新验证 keyring 账号，Issue #39 创建成功 |
| Ruff 报告 `tests/test_architecture.py` 存在未使用的 `AppContext` 导入 | 1 | 删除未使用导入后重新执行静态检查 |

### 阶段 1：Audio Feature 模板

目标：让音频功能从此不再需要修改 Shell。

目标结构：

```text
app/features/audio/
  page.py
  controller.py
  service.py
  repository.py
  organizer.py
  audit.py
  debank_adapter.py
  worker.py
  widgets/audio_tree.py
```

实施顺序：

1. 将现有 `audio_library`、`audio_repository`、`album_map` 通过兼容导出接入 Audio Service。
2. 将 `AudioDecryptWorker` 的 bank 处理、分类、清理、审计和文件索引拆到 Service/Organizer/Audit/Adapter。
3. 将 `audio_view.py` 改为自有控件的 `AudioPage`，只发出语义信号。
4. 将播放器状态、选择状态、列表缓存和进度交给 `AudioController`，Page 只展示状态。
5. MainWindow 只注册 Audio Feature，不保留音频字段、音频 Worker 和音频私有回调。

验收：音频树选择、播放、取消、批量解密、增量缓存、BGM 审计和未读标记回归通过；模拟修改音频树逻辑的变更不产生 `shell/main_window.py` diff。

### 阶段 2：Characters Feature 与 parser 拆分

目标：角色页面和 Lua 解析成为独立 ownership 区域。

实施内容：

- 建立 `CharacterPage`、`CharacterController`、`CharacterService` 和 Repository 组合。
- 保留 `app.core.character_loader.load_character_data` 作为兼容入口。
- 将 parser 按 `common/words/cards/skills/progression/items/assembler` 拆分。
- 角色解析弹窗、增量快照、未读状态和角色 Wiki 展示通过 Service Result 传递，不由 Shell 直接控制。
- 角色页面不再返回 `controls_dict`，不再连接 `parent._xxx`。

验收：BaseSkill、BaseCard、文本映射、突破和角色 Wiki 回归通过；模拟新增技能解析规则时 Shell、Audio、Version 0 diff。

### 阶段 3：Versions/Download Feature

目标：版本列表、版本检查、下载和 Bundle 状态独立。

实施内容：

- 抽出版本仓库、下载计划、状态同步、更新检查和版本清理服务。
- 建立 `VersionPage` 和 `VersionController`，Page 自己持有表格和选择状态。
- 下载 Worker 只执行 Service 计划并回报结果；进度由通用 `TaskHost` 展示。
- 版本数据与音频、角色、预览通过结果契约通信，不互相导入内部实现。

验收：下载、增量更新、Bundle 选择、删除版本、版本统计和最新版本标记回归通过；版本页内部变更不触碰其他 Feature。

### 阶段 4：Importer Feature 与后处理注册表

目标：清理最大的流程耦合。

实施内容：

- 将 `ImportASWorker` 拆为 `ImportService`、`BundlePrepareService`、`AssetStudioAdapter`、`ExportPlanner`、`PublishService` 和薄 `ImportWorker`。
- 将 `EXPORT_CATEGORIES` 改为各 Feature 提供的 `ExportSpec`，Importer 只处理统一规格。
- ImportService 产出明确的 `ImportResult`：成功分类、失败分类、临时目录、已发布产物、取消状态和后处理输入。
- 建立显式 `PostProcessorRegistry`：Lua/角色和音频后处理器按结果分类执行；不引入无边界全局 EventBus。
- 将 AS 外部进程、超时、输出解析和失败现场统一放进 Platform/Adapter。

验收：Lua 自动角色解析、音频自动后处理、分类导出、精准 AB 包选择、取消和事务回滚回归通过；新增一个导出分类只修改对应 Feature 的 `ExportSpec` 和测试。

### 阶段 5：Preview/Spine/FGUI Feature

目标：完成最后一个主要 UI/外部工具域迁移。

实施内容：

- 建立 Preview Page/Controller/Service/Worker。
- 将 `preview_catalog`、FGUI 图集逻辑、Spine 适配器和视频导出集中到 Preview 域。
- 共享 UI 只保留通用页头、命令栏、空状态、进度和主题令牌；角色立绘树、预览筛选和图集逻辑不进入共享层。
- 旧 `ui/features`、`ui/views` 仅保留兼容导入，调用方切换后再删除。

### 阶段 6：Platform、Shared 与旧 core 收缩

目标：让基础设施和领域代码不再混在 `core` 大目录。

实施内容：

- `path_utils`、`file_utils`、`process_runner`、`database`、`logger`、`environment`、`crash_reporter` 迁入 Platform，并保留兼容导出。
- `theme.py` 拆为语义令牌、主题应用和共享控件样式；通用 Qt 控件迁入 `shared/qt`。
- 各 Feature 的 Repository 只依赖 Platform 的连接/文件能力，不直接从 UI 读取全局路径或数据库。
- 旧 `app/core` 和 `app/ui/asset_browser.py` 进入兼容期，不再接受新功能；确认调用方、测试和文档全部切换后再删除。

### 阶段 7：文档和工作记录降冲突

目标：避免代码拆开后又在文档中重新冲突。

实施内容：

- 每个 Issue 使用 `docs/worklog/issue-<id>-<slug>/findings.md`、`progress.md`、`verification.md`。
- 顶层 `task_plan.md` 只保留当前架构索引和任务入口，不再记录所有开发者的过程细节。
- 每个 PR 添加独立 `changes/issue-<id>-<slug>.md`；版本发布时统一整理为 `版本历史.md`。
- README 只保留用户使用内容；开发指南只保留当前有效架构和开发约束；历史推理与验证放在 Issue worklog。

### 阶段 8：最终 ownership 验收

必须通过以下情景验证，而不是只看目录和行数：

1. 修改音频树选择逻辑，只改 `features/audio/**` 和对应测试。
2. 增加 BaseSkill 解析规则，只改 `features/characters/parser/**` 和对应测试。
3. 修改角色页面布局，只改 `features/characters/**` 或明确的 `shared/qt/**`。
4. 修改版本下载策略，只改 `features/versions/**` 和对应 Platform 契约测试。
5. 修改导入分类，只改 `features/importer/**` 与对应 Feature 的 `ExportSpec`。
6. Shell 只在新增/删除完整 Feature、导航协议或全局任务宿主时才变化。

### 提交与回滚策略

- 每阶段拆成多个本地、可回滚的小提交；不把每个检查点都推送成独立 PR。
- 每个迁移阶段单独 Issue/PR，禁止把 UI 美化、数据格式变化和结构迁移混在一起。
- 迁移期间保留旧模块兼容导出；每次切换调用方后运行全量测试和 Windows smoke。
- 发现输出、日志、取消或外部工具行为变化时，先回滚调用方切换，不删除旧实现，保留双入口排查。
- 最终所有阶段完成后，再统一做文档收口、差异审查和一个完整 PR。

## 2026-08-24 Issue #41：Characters Feature ownership 迁移

- [completed] 设计 CharacterPage、CharacterController、CharacterService 的兼容迁移切片。
- [completed] 迁移角色页面控件与语义信号，移出 MainWindow 的列表、筛选和详情状态。
- [completed] 迁移角色数据恢复、手动解析、自动解析结果、未读和 CSV 导出协调；自动解析继续复用导入进度弹窗。
- [completed] 保留 `app/ui/views/character_view.py` 兼容工厂，调用方已切换到 Feature 页面，未改变旧导入入口。
- [completed] 完成全量 pytest 100 passed、Ruff、AST 解析 116 个 Python 文件、真实 MainWindow smoke 和 `git diff --check`；下一步由用户正常启动确认后创建本地检查点。

## 2026-08-24 Issue #42：Versions/Download Feature ownership 迁移

- [completed] 创建 Issue #42，明确版本工作区、更新检查、下载、Bundle 状态和删除的 ownership 边界。
- [completed] 建立 `VersionPage`、`VersionController`、`VersionService` 和功能域 Worker 入口。
- [completed] 将 MainWindow 的版本页面、下载计划、更新检查和删除入口切换到 Versions Feature；保留导入流程所需的选中版本适配。
- [completed] 完成全量测试、Ruff、AST 解析 122 个 Python 文件、Windows smoke、文档同步和 diff 检查；待用户正常启动验证后建立本地检查点。

## 2026-08-24 Issue #43：Importer Feature 与后处理注册表

- [in_progress] 建立导出规格、ImporterService、ImportController 和兼容 Worker 边界。
- [pending] 将导入结果统一为 `ImportResult`，明确成功分类、失败分类、取消状态、发布产物和 Lua/音频后处理输入。
- [pending] 保持精准 Bundle 筛选、AssetStudio 暂存发布、Lua 版本隔离、音频后处理和取消语义不变。

### P4 检查点结果

- [completed] 新增 `ExportSpec`、`ImporterService`、`ImportController`、`PostProcessorRegistry` 和功能域 Worker 兼容入口；分类规则不再由 MainWindow 或旧 Worker 独占。
- [completed] `ImportResult` 已携带 Lua 发布结果与后处理分类；MainWindow 通过控制器接收进度和结果，旧导出、取消、暂存发布和精准 Bundle 筛选契约保持不变。
- [completed] 完成导入规格/结果/后处理/架构测试、全量 pytest、Ruff、AST 解析 98 个 Python 文件、实际 Qt 启动冒烟和 `git diff --check`。
- [completed] 本地检查点提交待完成；提交后进入用户验证，下一阶段为 Preview Feature。

## 2026-08-24 Issue #44：Preview Feature 与外部工具边界

- [in_progress] 审查图片预览、FGUI 图集、Spine 合成和视频导出之间的实际调用关系。
- [pending] 建立 Preview Page、Controller、Service、Worker 入口，收口预览目录、筛选、导出和外部工具结果。
- [pending] 保持 `output/character`、`output/fgui`、`output/video`、取消语义和旧入口兼容；不在本阶段重写算法或删除旧 Worker。

### P5a 检查点：Preview 页面与图片链路

- [completed] 新增 `PreviewPage`、`PreviewService`、`PreviewController`、预览项构造和功能域 Worker 入口。
- [completed] MainWindow 已改为装配 Preview Feature；图片目录扫描、角色筛选、缩略图加载、立绘导出任务生命周期由 Feature 控制器管理。
- [completed] 保留 `preview_view.py`、`preview_controller.py`、四类旧 Worker 作为兼容入口；未改变 `output/character` 和 Spine 导出行为。
- [completed] 完成 Preview 目录/架构测试、全量 pytest、Ruff、Qt 启动冒烟和 diff 检查；用户验证通过后继续 P5b 的 FGUI/Spine/视频导出边界收口。

### P5b 当前执行：FGUI / Spine / 视频导出入口

- [in_progress] 将 `export_controller.py` 的导出编排切换到 Preview Feature，旧 UI 路径保留兼容转发。
- [pending] 将 FGUI 图集切割、Spine/FFmpeg 适配和批量 Worker 入口纳入 Preview ownership。
- [pending] 补充导出计划、输出路径、取消和兼容入口测试；不改变 `output/fgui`、`output/video` 和角色图片输出契约。

### P5b 检查点结果

- [completed] 导出编排已切换到 `app/features/preview/export_controller.py`；`app/ui/features/export_controller.py` 保留为兼容转发。
- [completed] FGUI 图集和 Spine/FFmpeg 适配新增 Preview Feature 入口，批量/合成 Worker 通过 Feature Worker 入口装配；不改变外部工具参数与 output 契约。
- [completed] 完成兼容导入、架构边界、全量 pytest、Ruff、AST 解析 139 个 Python 文件、实际 Qt 启动冒烟和 diff 检查。
- [completed] P5 主要 UI/导出 ownership 迁移完成；外部工具 Platform 适配的最终下沉列入下一阶段 Platform/Shared 收缩。

## 2026-08-24 Issue #45：P6-P8 Platform/Shared 收缩与 ownership 最终验收

- [completed] P6：建立 Platform 与 Shared Qt 的稳定入口和依赖方向门禁，保留旧模块兼容。
- [completed] P7：按 Issue 整理 worklog/change fragment，收口 README、开发指南、版本历史和根计划索引。
- [completed] P8：执行 Feature ownership 场景验收、公共文件冲突审查和最终全量质量门禁。

详细调查、进度和验证记录见 `docs/worklog/issue-45-platform-shared/`；面向评审的变更摘要见 `docs/changes/issue-45-platform-shared.md`。
