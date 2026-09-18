# 图片预览输出管线与资源浏览器设计

## 目标

修正图片资源处理时机、输出目录和预览数据来源，使 AS 解包完成后就自动生成可交付资源，同时保留原始 Spine 资源和最终导出的 PNG。图片预览界面只读取已经生成的输出索引，不在切换页面时执行重型解包、查询或图集切割。

## 已确认的行为

1. `data/material` 是解包后的临时来源目录，保留现有来源数据。
2. `output` 是最终交付目录，必须同时包含：
   - 原始 Spine 资源副本；
   - 按角色和皮肤导出的最终 PNG；
   - 已切割的 FGUI 图集；
   - `burst-head` 大头照；
   - 统一资源索引和新状态。
3. AS 解包提交完成后，自动执行图片资源预处理；预处理结束前不能报告整个导入流程完成。
4. 进入图片预览页不能再次触发 Spine 查询、图集切割或 Burst Head 处理。
5. 角色、皮肤、图集和大头照使用稳定资源身份和统一的新状态，不能只依赖显示文件名。
6. Spine 导出默认是静态图；动画导出必须由用户在 UI 中主动选择。
7. 角色导出立绘和游戏素材以 Windows 大图标式文件夹浏览；Spine 资源继续使用树形勾选列表。

## 输出契约

```text
output/
├── spine/
│   └── <character_id-or-unmatched>/
│       └── <source_key>/
│           ├── *.skel / *.skel.bytes
│           ├── *.atlas / *.atlas.txt
│           └── 关联纹理和必要的源文件
├── character/
│   └── <character_id>/
│       └── <skin_key>/
│           ├── static.png 或 <animation>.png
│           └── metadata.json
├── fgui/
│   └── <package>/
│       ├── 已切割的 sprite PNG
│       └── cut_info.json
├── game_material/
│   └── burst-head/
│       └── 最终 PNG
├── preview_index.json
└── preview_state.json
```

原始 Spine 资源按源资源组复制一次，不按内部皮肤重复复制。`source_key` 使用规范化来源相对路径和内容指纹生成；复制时保留 `.skel`/`.skel.bytes`、`.atlas`/`.atlas.txt` 以及关联纹理，避免只复制骨骼文件后无法复用。

皮肤导出仍按 `character_id/skin_key` 独立保存。一个 Spine 文件内的多个内部皮肤不会因为文件名相同而互相覆盖。无法可靠确认角色 ID 的源资源进入 `output/spine/unmatched`，并在索引中记录原因。

## 处理时机与生命周期

AS 导入完成后的流程固定为：

```text
AS 解包并提交 data/material
        ↓
图片资源预处理 Worker
        ├─ 发布原始 Spine 到 output/spine
        ├─ 查询 Spine 角色和皮肤
        ├─ 切割 FGUI 图集到 output/fgui
        ├─ 提取 Burst Head 到 output/game_material/burst-head
        └─ 写入 preview_index.json 和 preview_state.json
        ↓
音频、角色和图片后处理全部结束
        ↓
关闭共享进度窗口并报告导入完成
```

图片预处理必须复用现有导入后处理的共享进度、取消、失败和状态机制。任务必须防止重复启动；取消时保留已经完成的输出，不删除其他分类的既有交付物。

预览页加载时只读取 `preview_index.json` 和 `output` 下的最终文件。如果索引缺失或版本不匹配，只显示明确的“需要重新处理”状态，不在 UI 线程内隐式执行完整预处理。

## 三个分页

### 角色 Spine

树结构为“角色 → 皮肤 → 源文件”。角色和皮肤可勾选，状态显示“新”、缺失 atlas、查询失败或未匹配。名称列设置较大的最小宽度并提供完整 tooltip，避免源文件名被截断。用户勾选后打开导出配置。

### 角色导出立绘

以 `output/character` 为数据源，根层显示角色文件夹，进入后显示皮肤文件夹，再进入显示最终 PNG。文件夹和文件采用大图标卡片，显示数量、导出状态和“新”标记；不再递归平铺成一个长列表。

### 游戏素材

根层显示 `burst-head` 和各 FGUI 图集包文件夹。进入文件夹后只显示 `output` 中已经切割完成的 PNG，禁止使用 `data/material` 中的原始 atlas PNG 作为预览内容。

## 导出配置

导出配置使用控件选择，不要求输入命令行参数。默认选择“静态图”，静态命令不携带动画选择参数并只导出时间 0 的一帧；动画模式才显示动画选择和帧率配置。导出期间按钮显示当前文件名和 `x/N`，重复点击无效，取消后恢复可操作状态。

## 新状态同步

所有分页共用 `PreviewResourceState`。状态键来自资源身份、源指纹和皮肤附件指纹，不使用 PNG 文件名作为唯一键。勾选、打开、导出完成和“全部标为已读”后，立即刷新当前节点、父文件夹、页签计数和底部状态；不能依赖重启才能消除残留的“新”。

## 验证要求

实现前先写失败测试，覆盖：

- 原始 Spine 被复制到 output 且来源仍保留；
- 皮肤 PNG 和元数据按角色/皮肤分目录；
- FGUI 页面只读取切割输出；
- AS 后处理顺序、进度、取消和重复启动保护；
- 预览页加载不触发重型处理；
- 静态导出默认为无动画参数；
- 大图标目录浏览和状态同步；
- 旧 output 兼容读取。

验证使用项目内 Python：

```text
E:\All-Projects\XL\.venv\Scripts\python.exe -m pytest -q
E:\All-Projects\XL\.venv\Scripts\python.exe -m ruff check app tests
E:\All-Projects\XL\.venv\Scripts\python.exe -m compileall -q app
git diff --check
```
