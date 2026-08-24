"""版本检查结果与下载状态持久化，不依赖 Qt。"""

from datetime import datetime
import os

from app.platform import database as db
from app.platform.diagnostics import logger


def register_checked_version(version_manager, info, versions, new_hashes, delta):
    """持久化检查到的新版本；已存在时返回 None。"""
    timestamp = versions["timestamp"]
    if timestamp in {row[0] for row in db.get_all_versions()}:
        return None

    version_manager.register_version(timestamp, info, versions)
    db.save_sub_bundles(timestamp, new_hashes)
    notes = f"新增 {len(delta['added'])} | 移除 {len(delta['removed'])} | 未变 {delta['common']}"
    db.add_notes(timestamp, notes)
    logger.info(
        f"检查更新：发现新版本 {timestamp}，新增 {len(delta['added'])} 个 bundle "
        f"（移除 {len(delta['removed'])}，未变 {delta['common']}）"
    )
    return {
        "timestamp": timestamp,
        "notes": notes,
        "added": len(delta["added"]),
    }


def record_downloaded_bundle(timestamp: int, bundle_hash: str, local_path: str) -> None:
    """将单个下载完成的 Bundle 路径写回数据库。"""
    connection = db.get_conn()
    try:
        connection.execute(
            "UPDATE sub_bundles SET local_path=?, downloadable=1 "
            "WHERE hash=? AND version_timestamp=?",
            (local_path, bundle_hash, timestamp),
        )
        connection.commit()
    finally:
        connection.close()


def append_changelog(output_path: str, message: str, timestamp: datetime | None = None) -> None:
    """向输出目录的变更日志追加一行。"""
    os.makedirs(os.path.dirname(output_path), exist_ok=True)
    current = timestamp or datetime.now()
    line = f"- {current.strftime('%Y-%m-%d %H:%M:%S')} | {message.replace(chr(10), ' ')}\n"
    with open(output_path, "a", encoding="utf-8") as handle:
        handle.write(line)
