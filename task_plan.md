# Task Plan: XL 界面视觉重设计

## Goal
在不破坏现有功能、数据契约和多人协作边界的前提下，重新审视 XL 的桌面应用视觉系统：保留可用的新蓝灰主题，去除明显的 AI 生成感，建立统一、可维护、可渐进落地的主题、布局、组件和交互视觉规范，并分阶段完成界面优化。

## Next Step
完成 Issue #30 的对话框与主窗口局部视觉收口，并在用户实机验证后再进入全局主题与 PR 收尾。

## Current Phase
Phase 3 - 页面视觉迁移

## Phases

### Phase 1: Requirements & Discovery
- [x] Understand user intent
- [ ] Read existing UI and theme implementation
- [ ] Inventory all top-level pages, dialogs, shared widgets, and theme entry points
- [ ] Document visual problems, constraints, and non-goals in findings.md
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
- [ ] Issue #30：用户实机验证与对话框操作回归
- **Status:** in_progress

### Phase 4: Testing & Verification
- [x] Run compile/import/lint checks
- [x] Launch the application and inspect screenshots/interactions（用户已确认第一阶段）
- [x] Launch and inspect the second-stage page shell（用户已验证）
- [x] 验证角色详情区空状态与选中后的详情切换（用户已验证）
- [x] 验证 Wiki 详情分区与技能数字高亮（用户已验证）
- [x] 验证 Issue #29 的图片选择反馈、音频空状态/播放文本、版本工作区样式（用户已验证）
- [ ] 验证 Issue #30 的图片查看器、角色选择、导出设置和右键菜单
- [x] Validate theme compatibility and major workflow regression（35 项测试 + 页面构建）
- [x] Record fresh verification evidence
- **Status:** pending

### Phase 5: Delivery
- [x] Update required project documentation（已同步角色 Wiki 首版结构与边界）
- [ ] Review diff for scope creep and collaboration conflicts
- [ ] Report results and wait for user confirmation before PR
- **Status:** pending

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

## Errors Encountered
| Error | Resolution |
|-------|------------|
| 初次增量补丁无法匹配脚本生成的模板行 | 改用完整文件重建，保留规划文件用途与编码。 |
