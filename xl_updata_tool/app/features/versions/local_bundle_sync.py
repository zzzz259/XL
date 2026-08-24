"""根据磁盘实际 Bundle 校准数据库下载状态。"""

import os

from app.platform import database as db
from app.platform.diagnostics import logger


def sync_local_bundles(bundles_dir: str, timestamp: int | None = None) -> int:
    """同步 Bundle 的 ``local_path``/``downloadable``，返回变更条数。

    磁盘是事实来源：磁盘存在的 Bundle 标记为已下载，数据库仍指向但文件
    已不存在的记录清空。可通过 timestamp 限定单个版本。
    """
    total_changed = 0
    for version in db.get_all_versions():
        version_timestamp = version[0]
        if timestamp is not None and version_timestamp != timestamp:
            continue

        bundle_dir = os.path.join(bundles_dir, str(version_timestamp))
        if not os.path.isdir(bundle_dir):
            continue
        on_disk = {
            filename[:-len(".bundle")]: os.path.join(bundle_dir, filename)
            for filename in os.listdir(bundle_dir)
            if filename.lower().endswith(".bundle")
        }
        sub_bundles = db.get_sub_bundles(version_timestamp) or []
        connection = db.get_conn()
        changed = 0
        for bundle_hash, _downloadable, local_path in sub_bundles:
            if bundle_hash in on_disk:
                if not local_path:
                    connection.execute(
                        "UPDATE sub_bundles SET local_path=?, downloadable=1 "
                        "WHERE hash=? AND version_timestamp=?",
                        (on_disk[bundle_hash], bundle_hash, version_timestamp),
                    )
                    changed += 1
            elif local_path:
                connection.execute(
                    "UPDATE sub_bundles SET local_path=NULL, downloadable=0 "
                    "WHERE hash=? AND version_timestamp=?",
                    (bundle_hash, version_timestamp),
                )
                changed += 1
        connection.commit()
        connection.close()
        if changed:
            logger.info("[同步] 版本 %s 下载状态：%s 条变更", version_timestamp, changed)
            total_changed += changed
    return total_changed
