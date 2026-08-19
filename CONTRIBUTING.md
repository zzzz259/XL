# 贡献指南（CONTRIBUTING）

欢迎参与本项目。由于团队刚起步，本文档约定一套**最小可行的协作规范**，帮助双方对齐意图、避免冲突。

## 一、协作流程（GitHub Flow）

```
main（稳定，始终可运行）
  ├── feature/xxx   ← 每个功能/修复开一个分支
  └── fix/xxx
```

**日常循环：**

```bash
git pull origin main              # 1. 开工前同步
git checkout -b feature/图片预览   # 2. 从最新 main 切分支
# ... 开发，小步提交 ...
git push -u origin feature/图片预览 # 3. 推送分支
# 4. 发 Pull Request，@对方 review
# 5. review 通过 → 合并到 main → 删除分支
```

**铁律：**
- **禁止直接 push 到 main**，一切改动走 PR。
- **改 `app/core/`（底层）前必须开 Issue 打招呼**，双方确认后再动。
- **改同一个文件前先沟通**，避免同时改造成冲突。

## 二、Issue 规范（记录"要做什么"）

每个功能/修复/重构，先开一个 Issue，写清楚四要素：

```markdown
### 背景（为什么做）
现在只支持 png，但资源里也有 webp 格式，预览不了。

### 要做什么（做什么）
- ImageLoadWorker 增加 webp 解码
- 缩略图和大图预览都支持

### 不做（边界）
- 不做 webp 动画
- 不改导出逻辑

### 验收标准（怎么算做完）
- 拖入 webp 能正常预览
- png 功能不回归
```

**目的**：对方看 Issue 就知道你的意图、边界、验收标准，不用猜。

## 三、Pull Request 规范（记录"改了什么、为什么"）

发 PR 时写清楚：

```markdown
### 改动摘要
给 ImageLoadWorker 加了 webp 支持，用 QImageReader 自动识别格式。

### 改了哪些文件
- app/ui/workers/image_worker.py：加载逻辑改用 QImageReader

### 为什么这么改
QImageReader 自动识别 png/webp/jpg，不用每种格式写一套判断。

### 测试
- 手动拖入 webp 和 png，都能预览
- 缩略图正常

### 关联 Issue
Closes #12
```

**合并规则**：至少一方 review 通过后才能合并。

## 四、Commit Message 规范

用前缀区分类型，便于追溯：

```
feat:     新功能       feat: 图片预览支持 webp
fix:      修 bug       fix: 修复缩略图内存泄漏
refactor: 重构         refactor: 抽出 spine 导出逻辑
docs:     文档         docs: 更新开发文档
chore:    杂务         chore: 清理缓存文件
```

**要求**：commit 要**小而频繁**，一个 commit 只做一件事，别攒一大坨。

## 五、代码规范

- 缩进 4 空格（与现有代码一致）
- 注释用中文，解释"为什么"而非"是什么"
- 魔法数字尽量抽成模块顶部常量
- 新增独立功能放 `app/ui/features/` 或 `app/ui/workers/`，不要继续往 `main_window.py` 堆

## 六、沟通约定

- **不确定对方意图 → 直接问**，宁可多问一句"你是想做 X 对吧"。
- **卡住 / 要改公共文件 → 在 Issue 里说一声**，别默默改。
- **每天开工前** `git pull origin main`，并扫一眼对方的新提交和新 Issue。
- **合并后** 及时删分支，保持仓库整洁。

## 七、测试文件约定

每个工具项目的测试放在对应项目目录下，与 `app/` 平级；本仓库当前项目使用 `xl_updata_tool/tests/`。项目自己的 `pyproject.toml`、运行依赖和开发依赖也放在项目目录内，仓库根目录的 CI 负责统一编排。

测试产生的文件（下载的 ab 包、导出的资源、临时脚本）请放这些目录，会被 `.gitignore` 自动忽略，**不用删、不会提交**：

```
data/bundles/    # 下载的 ab 包
output/          # 导出的资源
_tmp/            # 随手写的临时脚本/测试数据
logs/            # 日志
```

只要落在这几个目录里，`git add .` 和 `git status` 都自动跳过，只管测试不用管清理。
