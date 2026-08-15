from . import database as db
from .logger import logger


class VersionManager:
    def __init__(self):
        self._cache = {}

    def register_version(self, timestamp, update_info, versions_data, is_current=True):
        db.save_version(timestamp, update_info, versions_data, is_current)
        if is_current:
            prev = self._get_prev_version(timestamp)
            if prev:
                logger.info(f"注册版本 {timestamp}：找到上一版本 {prev}，记录变更")
                db.record_changes(prev, timestamp)
            else:
                logger.info(f"注册版本 {timestamp}：无上一版本，跳过变更记录")
        self._cache.pop("versions", None)

    def get_versions(self):
        if "versions" not in self._cache:
            self._cache["versions"] = db.get_all_versions()
        return self._cache["versions"]

    def get_current(self):
        return db.get_current_version()

    def get_bundles(self, timestamp, name_filter=None, limit=None, offset=None):
        return db.get_bundles_for_version(timestamp, name_filter, limit, offset)

    def get_bundle_count(self, timestamp):
        return db.get_bundle_count(timestamp)

    def get_changes(self, from_ts, to_ts):
        return db.get_version_changes(from_ts, to_ts)

    def get_bundle_history(self, name):
        return db.get_bundle_history(name)

    def compare_versions(self, ts1, ts2):
        v1_bundles = {r[0]: r for r in db.get_bundles_for_version(ts1)}
        v2_bundles = {r[0]: r for r in db.get_bundles_for_version(ts2)}
        all_names = set(v1_bundles.keys()) | set(v2_bundles.keys())
        added, removed, changed, unchanged = [], [], [], []
        for name in all_names:
            in_v1 = name in v1_bundles
            in_v2 = name in v2_bundles
            if in_v1 and not in_v2:
                removed.append({"name": name, "old": v1_bundles[name]})
            elif not in_v1 and in_v2:
                added.append({"name": name, "new": v2_bundles[name]})
            elif v1_bundles[name][3] != v2_bundles[name][3]:
                changed.append({"name": name, "old": v1_bundles[name], "new": v2_bundles[name]})
            else:
                unchanged.append({"name": name, "info": v1_bundles[name]})
        logger.info(f"比较版本 {ts1} vs {ts2}：新增 {len(added)}，移除 {len(removed)}，变更 {len(changed)}，未变 {len(unchanged)}")
        return {"added": added, "removed": removed, "changed": changed, "unchanged": unchanged}

    def update_notes(self, timestamp, notes):
        db.add_notes(timestamp, notes)
        self._cache.pop("versions", None)

    def refresh(self):
        self._cache.clear()
        return self.get_versions()

    def _get_prev_version(self, current_ts):
        versions = self.get_versions()
        for v in versions:
            if v[0] < current_ts:
                return v[0]
        return None
