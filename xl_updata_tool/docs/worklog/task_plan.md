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

#### 调查错误

| 错误 | 原因 | 处理 |
|---|---|---|
| 读取 `_subcontractors/_epic7_defsb.py` 失败 | 实际入口位于 `tools/epic7_debank_v1_0/_epic7_defsb.py`，不在 `_subcontractors/` | 已改用仓库文件清单定位真实入口，继续只读调查 |
| 更新 `progress.md` 首次补丁未匹配 | 目标上下文文字与文件尾部实际内容不一致 | 重新读取文件尾部后改用精确上下文追加，已完成记录 |
| `compileall` 写入仓库 `__pycache__` 被拒绝 | 仓库已有缓存目录 ACL 不允许当前账户覆盖临时 `.pyc` | 将下一次编译缓存重定向到 Codex 工作目录 `tmp`，不修改仓库权限 |
| 全量 pytest 固定 basetemp 无法清理、Ruff 写 `.ruff_cache` 被拒绝 | 仓库/旧临时目录 ACL 限制，验证工具默认写入受限位置 | 改用新的 Codex 临时目录，并设置 `RUFF_CACHE_DIR` 到 Codex 工作目录后重跑 |
| 全量 pytest 暴露旧测试 fake `epic7_debank.run()` 不接受 `use_cache` | 生产调用新增了缓存控制参数，测试替身接口未同步 | 补齐测试替身参数后重跑全量门禁 |
