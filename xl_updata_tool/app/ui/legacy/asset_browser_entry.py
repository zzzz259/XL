"""旧 AssetBrowser 的唯一入口。

主流程使用 ImportASWorker；这个模块只为历史调用保留兼容入口，便于后续
在不影响主流程的前提下删除或单独维护旧资源浏览器。
"""

from app.ui.asset_browser import AssetBrowser


def open_legacy_asset_browser(parent, bundle_paths, timestamp):
    """打开旧版资源浏览器，并返回对话框执行结果。"""
    browser = AssetBrowser(parent, bundle_paths, timestamp)
    return browser.exec()
