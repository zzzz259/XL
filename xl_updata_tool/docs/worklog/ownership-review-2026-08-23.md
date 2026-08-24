# XL 多人协作代码所有权审查报告

日期：2026-08-23

## 结论摘要

第三方报告指出的核心问题成立：XL 已经完成了部分技术分层，但还没有完成按功能域划分的代码所有权。`MainWindow` 仍然是多个功能域共同依赖的汇合点，`core` 仍混合领域逻辑与基础设施，Worker 和页面工厂仍保留跨层耦合。

本次复核不建议立即执行一次性目录搬迁。建议采用“功能域优先、域内分层、兼容迁移”的路线：先建立 `bootstrap`、`shell`、`application/features`、`platform`、`shared` 的稳定入口和架构测试，再以音频为模板迁移，随后迁移角色、版本、导入和预览。现有 `app/core`、`app/ui` 通过兼容导出逐步收缩，直到调用方全部切换后再清理。

本轮只完成审查和计划，没有修改源代码、移动目录或改变运行时契约。

## 当前事实核验

| 项目 | 当前事实 | 结论 |
|---|---|---|
| `main.py` | 约 2 KB，负责运行模式、日志、异常报告、Qt 应用启动和窗口创建 | 不是主要冲突点，继续保持轻量 |
| `main_window.py` | 109,460 bytes、约 2331 行、126 个方法 | 已成为跨领域 Merge Conflict Hub |
| `AudioDecryptWorker` | 22,427 bytes、约 483 行 | 同时承担 Worker、AudioService、Organizer、Audit、Repository 等职责 |
| `ImportASWorker` | 26,412 bytes、约 545 行 | 同时承担 Bundle 修复、AssetStudio、分类导出、事务提交、Lua 后处理 |
| `character_loader.py` | 63,378 bytes、约 1365 行 | 角色解析内部已有明显的 parser 子域 |
| `theme.py` | 24,005 bytes、约 914 行 | 共享 UI 资源与主题逻辑仍是公共热点 |
| `版本历史.md` | 80,940 bytes | 版本记录是文档协作热点 |
| 提交历史 | 全部 73 个提交，`main_window.py` 文件历史出现 27 次 | 不同领域功能经常必须修改主窗口 |

## 第三方报告逐条复核

### 1. 问题主要在 MainWindow，不在 main.py

结论：确认。

`main.py` 当前主要执行运行模式解析、日志初始化、异常报告安装、创建 `QApplication` 和 `MainWindow`。真正的耦合集中在 `MainWindow`：它同时持有版本、导入、音频播放器、角色仓库、多个 Worker、进度弹窗、数据库和输出路径状态。

处理方向：保持 `main.py` 轻量；新增 `bootstrap/app_factory.py` 作为组合根，由它装配运行时上下文和各 Feature，主窗口不再直接导入领域实现。

### 2. 当前是横向技术分层，不是功能域所有权分层

结论：确认。

音频相关代码目前分布在 `core/audio_library.py`、`core/audio_repository.py`、`core/album_map.py`、`ui/views/audio_view.py`、`ui/features/audio_controller.py`、`ui/workers/audio_decrypt.py` 和 `tools/epic7_debank_v1_0/`。角色、预览、版本和导入也采用类似分布方式。

处理方向：目标改为“功能域优先、域内再分层”，但迁移期不直接删除现有目录，避免一次性产生大量重命名冲突。

### 3. 功能域优先的目标结构

结论：方向采纳，结构采用渐进式版本。

第三方建议的 `bootstrap / shell / features / platform / shared` 方向合理。XL 的过渡目标如下：

```text
app/
  bootstrap/                 # 运行时和 Feature 装配
  shell/                     # MainWindow、导航、状态栏、任务宿主
  features/
    versions/                # 版本列表、检查更新、下载
    importer/                # Bundle、AssetStudio、导出计划
    audio/                   # 页面、服务、仓库、分类、审计、debank
    characters/              # 页面、服务、仓库、Lua parser
    preview/                 # 图片、FGUI、Spine 和视频导出
  platform/                  # 路径、文件系统、数据库、进程、网络、诊断
  shared/                    # 跨页面 UI、契约、事件和任务基础设施
  core/                      # 迁移期兼容层，逐步收缩
  ui/                        # 迁移期兼容层，逐步收缩
```

这里的重点不是目录名称，而是依赖方向和 ownership：音频开发者应主要修改 `features/audio/`，角色开发者应主要修改 `features/characters/`。

### 4. Page 与 MainWindow 的远程控件耦合

结论：确认，且是当前最直接的页面冲突来源。

`audio_view.py`、`character_view.py`、`preview_view.py` 和 `version_view.py` 都通过 `parent._private_method` 连接事件，并返回 `(container, controls_dict)`。随后 `MainWindow` 把各页面内部控件重新复制到自己的属性中。

处理方向：改为真正的 `QWidget` Page。Page 自己拥有控件，只暴露语义信号和 `set_*` 状态方法；Controller 连接 Page 与 Service；MainWindow 只注册 Page，不读取其内部控件。

### 5. Page 自己拥有控件

结论：采纳。

目标形式：

```python
class CharacterPage(QWidget):
    parse_requested = Signal()
    refresh_requested = Signal()
    export_requested = Signal()
    mark_all_read_requested = Signal()
    character_selected = Signal(str)

    def set_characters(self, characters): ...
    def show_profile(self, profile): ...
    def set_loading(self, loading): ...
```

Page 不暴露 `character_table`、`controls_dict`，也不调用 `parent._xxx`。这会让页面布局修改局限在自身 Feature 目录。

### 6. MainWindow 控制在 200～300 行

结论：作为方向指标采纳，不作为机械验收指标。

真正验收不应是行数，而应是：

- 音频树选择逻辑变更时，`shell/main_window.py` 0 diff；
- BaseSkill 解析规则变更时，Shell、Audio、Version 0 diff；
- 角色页面布局变更时，只修改 `features/characters/` 和必要的共享 UI；
- MainWindow 不再知道 bank、Lua、AssetStudio、debank、BaseCard 等领域概念。

### 7. Composition Root

结论：采纳。

`bootstrap/app_factory.py` 负责创建运行时上下文、平台适配器、服务、Controller 和 Feature Page。主窗口只接收已装配好的 Feature 列表和公共壳层能力。

仅在新增/删除完整 Feature 或改变公共装配协议时才修改组合根；音频、角色等 Feature 内部变化不应触碰它。

### 8. AudioDecryptWorker 需要二次拆分

结论：确认，优先级高。

当前 Worker 同时处理 `.bytes → bank`、文件扫描、Lua 专辑映射、debank、音频分类、旧文件清理、索引、BGM 审计、语音文件名归一化和临时目录清理。

目标拆分：

```text
AudioPage
  → AudioController
    → AudioService
       ├── AudioOrganizer
       ├── AudioRepository
       ├── AudioAudit
       └── DebankAdapter
  → AudioWorker              # 只做 QThread 和信号适配
```

### 9. ImportASWorker 是第二个小型单体

结论：确认，优先级高，但排在 Audio 模板之后。

当前 Worker 同时负责 Bundle 修复、AssetStudio map、分类导出、CLI、staging、事务替换、Lua 发布和反编译。`EXPORT_CATEGORIES` 也直接写在 Worker 中。

目标拆分为 `ImportService`、`BundlePrepareService`、`AssetStudioAdapter`、`ExportPlanner` 和 `PublishService`。各 Feature 只提供自己的 `ExportSpec`，Importer 只消费统一规格，不直接理解音频、角色或 Lua 的内部规则。

### 10. 导入后的连锁处理

结论：采纳简化版 `PostProcessorRegistry`，不引入通用 EventBus。

`ImportService` 返回带有分类和产物状态的 `ImportResult`，注册表根据分类调用 Lua/角色、音频等后处理器。Importer 不直接认识 Character，Character 不直接认识 Audio，MainWindow 不负责决定处理顺序。

注册表必须是显式的、可测试的、按任务生命周期创建的，避免隐式全局事件总线造成新的追踪困难。

### 11. character_loader.py 拆分 parser 子域

结论：确认，但在 Feature 外壳稳定后进行。

当前已经包含通用 `T()` 解析、Word、CV、LevelUp、Badge、Item、Quality、Skill、突破和最终角色聚合。建议拆为：

```text
features/characters/parser/
  common.py
  words.py
  cards.py
  skills.py
  progression.py
  items.py
  assembler.py
```

保留 `app.core.character_loader` 兼容导出，所有拆分完成并通过快照回归后再删除旧实现。

### 12. 共享 UI 与 Feature UI 分离

结论：采纳。

主题、页头、命令栏、空状态、通用进度对话框属于 `shared/qt/`；音频树、角色 Wiki、版本表、预览列表属于对应 Feature。不能因为都是 QWidget 就全部放进共享目录。

`theme.py` 本身也要拆成语义令牌、主题应用和公共样式片段，避免继续成为所有 UI 任务的共同修改点。

### 13. core 不应继续作为大仓库

结论：确认，但采用最后收缩而非立即搬迁。

当前 `core` 混合了音频、角色、版本、Bundle、数据库、日志、路径、文件、网络和进程。最终领域代码归 Feature，平台能力归 `platform`，`core` 只保留过渡兼容导出。

数据库不整体塞进某个 Feature：连接、事务和迁移进入 `platform/database.py`，具体版本/音频/角色查询由各自 Repository 负责。

### 14. 依赖方向写入 CI

结论：采纳。

目标方向：

```text
bootstrap
  ↓
shell + features
  ↓
shared + platform
```

需要固定的规则包括：

- Shell 不得导入 Feature 内部实现；
- Audio 不得直接导入 Character；
- Page 不得调用 `parent._private_method`；
- Page 不得向 Shell 返回 `controls_dict`；
- Worker 不得同时承担完整的文件遍历、子进程、Repository 和分类规则；
- Feature 之间通过契约、结果对象和显式后处理器通信；
- Shell 不得导入数据库、AssetStudio、debank、角色解析器等领域对象。

### 15. 文档与工作记录冲突

结论：确认，优先级中高。

当前 `版本历史.md` 约 81 KB，`findings.md`、`progress.md`、`task_plan.md` 也是多任务共享写入点。

目标采用 Change Fragment：

```text
changes/
  issue-35-audio-debank.md
  issue-32-character-parser.md
```

每个 Issue 使用独立工作目录：

```text
docs/worklog/
  issue-35-audio/
    findings.md
    progress.md
    verification.md
```

`版本历史.md` 由发布整理阶段统一合并；顶层计划只保留索引和当前架构基线，避免所有开发者共同编辑同一份过程记录。

### 16. 代码认领

结论：采纳。

初步 ownership：

| 领域 | 默认修改范围 |
|---|---|
| 音频 | `features/audio/**` |
| 角色 | `features/characters/**` |
| 图片/Spine/FGUI | `features/preview/**` |
| 版本/下载 | `features/versions/**` |
| AssetStudio 导入 | `features/importer/**` |
| 公共 UI | `shared/qt/**` |
| 日志/Debug | `platform/diagnostics/**` |
| App 壳层 | `shell/**` |
| 启动装配 | `bootstrap/**` |

`shell`、`bootstrap`、`platform` 和 `shared` 属于高协调区；普通 Feature PR 不应随意修改。具体 GitHub 审查人名单待用户确认后写入 `CODEOWNERS`。

### 17. 迁移顺序

结论：采用第三方建议的顺序，并增加 P0 架构门禁阶段：

1. 建立目标目录、Feature 契约、组合根草图和架构测试，不改变行为；
2. 迁移 Audio，作为完整 Feature 模板；
3. 迁移 Characters，并拆 parser；
4. 迁移 Versions/Download；
5. 迁移 Importer，拆 Service/Adapter/Worker；
6. 迁移 Preview/Spine/FGUI；
7. 完成 `core`、旧 `ui` 和旧 AssetBrowser 的兼容层收缩；
8. 启用 Change Fragment、分 Issue Worklog 和最终 ownership 门禁。

每个阶段都必须满足“功能域内部改动不触碰 Shell”的回归验收，不能只用行数或目录数量作为完成标准。

## 最终判断

第三方报告不是要求 XL 推倒重来，而是指出当前拆分停在“技术文件拆分”阶段，尚未达到“开发者可以按功能认领、互不碰撞”的阶段。报告中的目标方向可行，但必须采用兼容迁移，先做边界和契约，再做目录收缩。

## 本轮验证范围

- 已核对 `main.py`、`main_window.py`、三个主要 Worker、`character_loader.py`、页面工厂、导入分类和现有文档。
- 已确认第三方报告中的主要规模数据与当前仓库基本一致。
- 已确认当前没有 `CODEOWNERS` 或等价 ownership 文件。
- 本轮没有运行导出流程，没有修改源代码，没有移动目录，没有创建 GitHub Issue。
