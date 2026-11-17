# Portrait Content Notice Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 立绘只统计和推送新增及真实像素修改，结束通知显示内容数量而非任务执行状态。

**Architecture:** 在现有服务端发布及恢复边界保存每套图的内容变化分类并门控outbox/事件，按版本条目聚合。经ProcessResult和finish事件传给Bot格式器；图鉴链路不扩展。

**Tech Stack:** Python 3.11+、Pillow、pytest/unittest；保留现有文件manifest及持久outbox。

## Global Constraints

- 立绘只推新增和尺寸/RGBA像素确实变化的图；图鉴仍只推新增。
- 像素摘要为SHA256(big-endian width,height + row-major RGBA bytes)，文件SHA仅用于完整性。
- 新增统计字段new_count/modified_count；ProcessResult字段portrait_new_count/portrait_modified_count；finish.portraits字段new/modified。
- 内部success/failed/skipped保留；公开文案为“；新增立绘X，修改立绘Y”，旧事件无内容字段则省略此分句。
- 同版本重跑/中断恢复统计稳定，无变化不发图；不回填覆盖历史、不清receipt、不改角色图鉴推送、不改排行榜/客户端/部署器、不推送或部署。
- 独立工作树E:/FallStar/XL-worktrees/portrait-notice-1117；唯一基线96a022e89a8ae2ac8cc68e1ee402d365f156b958。

### Task 1: 内容判定、恢复与结束通知的完整修复

**Files:**
- Modify: `xl_updata_server/server_app/portrait_update.py`（发布门控、manifest字段、完整版本内容汇总与恢复）
- Create if useful: `xl_updata_server/server_app/portrait_pixels.py`（只含Pillow解码/尺寸RGBA摘要；不导入Bot包）
- Modify: `xl_updata_server/server_app/pipeline.py`（ProcessResult默认计数字段）
- Modify: `xl_updata_server/server_app/processor.py`（正常及恢复结果透传，finish事件字段）
- Modify: `xl_qqbot/bot_app/watcher.py`（结束通知格式）
- Test: `xl_updata_server/tests/test_portrait_update_unittest.py`
- Test: `xl_qqbot/tests/test_portrait_watcher_unittest.py`
- Modify tests only if contract requires: `xl_updata_server/tests/test_portrait_history_backfill_unittest.py`（回填不发实时通知）

**Interfaces:**
- Consumes: `_SkinTask.previous`, previous manifest version, verified PNG, current work/published manifests。
- Produces: `PortraitSummary.new_count/modified_count`、`ProcessResult.portrait_new_count/portrait_modified_count`、finish.portraits.new/modified；每套manifest保存像素SHA/内容分类/比较基线（具体字段名统一后在报告注明）。

- [ ] **Step 1: 先补失败测试并观察RED。** 用现有PortraitEnv与真实PNG字节，至少包括：

```python
# 新皮肤：首次有效图，不把旧not_generated当已有图片。
assert (summary.new_count, summary.modified_count) == (1, 0)
# 输入变化但RGBA相同（可重新压缩PNG）：更新产物但不发事件或outbox。
assert (summary.new_count, summary.modified_count) == (0, 0)
assert env.ready_events(version) == []
# 相同身份下像素或尺寸变化：仅该套修改。
assert (summary.new_count, summary.modified_count) == (0, 1)
```

补继承0/0、失败保旧0/0、无基线不补历史、缺旧有效图首次生成new、相同版本done/emit/截断恢复计数稳定且无同像素补发、回填不发通知。Bot测试finish new1/modified2时精确输出“；新增立绘1，修改立绘2”，0/0照实显示；旧事件无新字段不猜测。

- [ ] **Step 2: 跑定向测试，保存失败原因。** 原生Python可执行：在server目录`python -m pytest -q tests/test_portrait_update_unittest.py`；Bot目录`python -m pytest -q tests/test_portrait_watcher_unittest.py`，PYTHONPATH包含xl_qqbot和xl_gacha_web。现有LinuxQA venv也可复用。先确认缺功能导致断言失败，不把环境缺依赖当RED。

- [ ] **Step 3: 实施最小修复。** 用Pillow完整解码PNG并逐条带宽高计算RGBA SHA，失败不得回退到文件SHA假装像素比较。新图与旧已校验有效产物比较，写分类及基线到work/发布manifest；仅new/modified写outbox和portrait_ready。继承不携带上一版内容分类；done/emit保留本版本分类，旧work缺字段恢复时重新比较；无变化恢复不得反复被识别成待补事件。按完整manifest聚合内容数；ProcessResult两条返回路径透传；finish保留内部三字段再增加new/modified；Bot只在两个新字段齐全时拼内容分句。

- [ ] **Step 4: 跑同组测试GREEN，再做适当完整回归。** Server全套、Bot全套、compileall及变更文件lint、git diff --check；如原生平台测试存在既有差异，转现有WSL Python环境核验，遇E_ACCESSDENIED立即同命令提权。不得修改不相关业务以让环境通过。

- [ ] **Step 5: 自查并本地提交。** 审查所有写outbox/发portrait_ready与done/emit分支、保持旧事件兼容，核查仅允许文件及测试/规格计划变化。报告RED/GREEN命令与结果、真实边界、SHA、未完成风险；不推送、不连接服务器、不发送消息。

### 本地集成与审查

- [ ] 主代理复核任务报告、范围和精确测试结果，生成基线到候选完整diff。
- [ ] 独立sol高模式审查规格合规和代码质量；若有关键问题修复并重新验证。
- [ ] 主代理生成本地通知样例/验证记录，更新STATE并向用户报告，明确线上尚未部署和演习覆盖限制。
