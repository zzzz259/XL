"""版本功能域数据服务，不依赖 Qt。"""

from __future__ import annotations

import os
from pathlib import Path

from app.platform import database as db
from .local_bundle_sync import sync_local_bundles
from .seed_versions import seed_bundled_versions
from .version_cleanup import count_downloaded_bundles, delete_downloaded_bundles
from .version_data import compute_download_hashes, compute_version_delta_map
from .version_download import calculate_missing_downloads
from .version_manager import VersionManager
from .version_update import register_checked_version


class VersionService:
    """封装版本仓库、下载计划和磁盘 Bundle 状态。"""

    def __init__(self, bundles_dir: str | os.PathLike[str]):
        self.bundles_dir = Path(bundles_dir)
        self.manager = VersionManager()

    def seed(self) -> None:
        seed_bundled_versions(self.manager, str(self.bundles_dir))

    def refresh(self):
        return self.manager.refresh()

    def versions(self):
        return self.manager.get_versions()

    def current(self):
        return self.manager.get_current()

    def sync_local(self, timestamp=None):
        return sync_local_bundles(str(self.bundles_dir), timestamp)

    def bundles(self, timestamp):
        return db.get_sub_bundles(timestamp)

    def delta_map(self, versions) -> dict:
        hashes_by_version = {
            row[0]: (item[0] for item in (db.get_sub_bundles(row[0]) or []))
            for row in versions
        }
        return compute_version_delta_map((row[0] for row in versions), hashes_by_version)

    def delta_hashes(self, timestamp) -> set[str]:
        versions = self.versions()
        hashes_by_version = {
            row[0]: (item[0] for item in (db.get_sub_bundles(row[0]) or []))
            for row in versions
            if "(delta" not in (row[10] or "")
        }
        return compute_download_hashes(timestamp, (row[0] for row in versions), hashes_by_version)

    def missing_downloads(self, timestamp, delta_only=True):
        sub_bundles = self.bundles(timestamp)
        all_hashes = {row[0] for row in sub_bundles}
        target = self.delta_hashes(timestamp) if delta_only else all_hashes
        return sub_bundles, calculate_missing_downloads(sub_bundles, target)

    def register_checked(self, info, versions, new_hashes, delta):
        return register_checked_version(self.manager, info, versions, new_hashes, delta)

    def delete_version(self, timestamp) -> int:
        return delete_downloaded_bundles(timestamp, self.bundles(timestamp))

    def downloaded_count(self, timestamp) -> int:
        return count_downloaded_bundles(self.bundles(timestamp))
