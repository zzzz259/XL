# Issue #47 调查结论

## 勾选失效

- `AudioController.on_item_clicked()` 在 `itemClicked` 回调中手动设置复选框状态。
- Qt 点击复选框指示器时仍会执行默认切换；当前控制器改动与 Qt 默认切换存在事件顺序竞争。
- `itemPressed` 并不能覆盖所有点击位置，导致控制器有时拿不到稳定的点击前状态。
- 修复方向是把点击意图统一提交到事件循环末尾，并由逻辑选择集驱动可见节点状态。

## 加载卡顿

- `AudioService.load_catalog()` 同步执行递归扫描、`.audio_state.json` 快照同步和文件状态写回。
- `AudioController.load_catalog()` 随后在 GUI 线程一次性构造所有 `QTreeWidgetItem`。
- 实际 7211 个文件扫描约 0.5 秒，状态快照与完整树构造属于主要 UI 阻塞来源。
- 启动预热应只准备 Qt-free 的目录索引；页面只构造首层节点，展开时再构造下一层，避免把全部文件转换成 Qt 对象。

## 不变契约

- `output/audio/`、`.audio_state.json`、未读状态、导出和播放器行为保持不变。
- “刷新列表”仍然是用户主动强制刷新入口；后台预热失败时页面保留可重试的错误状态。
