# Issue #46 调查结论

- 四个页面作为兄弟项共同放在内容区 `QVBoxLayout` 中。
- 页面切换时，`MainWindow._set_version_content_visible(False)` 只调用了版本页内部标题和表格的显隐方法。
- `VersionPage` 自身仍然可见并保留布局空间，因此当前页面被挤到下半区域；外层未绘制背景的位置表现为黑色空白。
- 该问题与资源数量、图片加载、音频解密和角色数据无关。
