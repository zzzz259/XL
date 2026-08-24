"""已下载版本文件清理，不依赖 Qt。"""

import os

from app.platform import database as db
from app.platform.diagnostics import logger


def count_downloaded_bundles(sub_bundles) -> int:
    """统计子 Bundle 中已有本地文件的数量。"""
    return sum(1 for row in sub_bundles if row[2])


def delete_downloaded_bundles(timestamp: int, sub_bundles) -> int:
    """删除指定版本的本地 Bundle，并清理数据库路径状态。"""
    downloaded = count_downloaded_bundles(sub_bundles)
    connection = db.get_conn()
    try:
        for row in sub_bundles:
            local_path = row[2]
            if not local_path:
                continue
            try:
                os.remove(local_path)
            except OSError as exc:
                logger.warning(f"删除 Bundle 失败 {local_path}: {exc}")
            connection.execute(
                "UPDATE sub_bundles SET local_path=NULL, downloadable=0 "
                "WHERE hash=? AND version_timestamp=?",
                (row[0], timestamp),
            )
        connection.commit()
    finally:
        connection.close()
    return downloaded
