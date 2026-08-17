import sqlite3
import json

from .logger import logger

DB_PATH = None


def init_db(path):
    global DB_PATH
    DB_PATH = path
    conn = sqlite3.connect(path)
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA synchronous=NORMAL")
    conn.execute("PRAGMA cache_size=-32000")
    conn.executescript("""
        CREATE TABLE IF NOT EXISTS versions (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            timestamp INTEGER UNIQUE NOT NULL,
            arts_ver INTEGER,
            data_ver INTEGER,
            other_ver INTEGER,
            video_ver INTEGER,
            apk_version TEXT,
            manifest_file TEXT,
            updateinfo_json TEXT,
            versions_json TEXT,
            is_current INTEGER DEFAULT 0,
            downloaded INTEGER DEFAULT 0,
            created_at TEXT DEFAULT (datetime('now','localtime')),
            notes TEXT
        );
        CREATE TABLE IF NOT EXISTS bundles (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT NOT NULL,
            hash TEXT NOT NULL,
            size INTEGER,
            ver INTEGER,
            version_timestamp INTEGER NOT NULL,
            local_path TEXT,
            FOREIGN KEY (version_timestamp) REFERENCES versions(timestamp),
            UNIQUE(name, ver, version_timestamp)
        );
        CREATE TABLE IF NOT EXISTS version_changes (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            from_timestamp INTEGER,
            to_timestamp INTEGER,
            bundle_name TEXT,
            from_ver INTEGER,
            to_ver INTEGER,
            from_hash TEXT,
            to_hash TEXT,
            size_diff INTEGER,
            created_at TEXT DEFAULT (datetime('now','localtime')),
            FOREIGN KEY (from_timestamp) REFERENCES versions(timestamp),
            FOREIGN KEY (to_timestamp) REFERENCES versions(timestamp)
        );
        CREATE TABLE IF NOT EXISTS sub_bundles (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            hash TEXT NOT NULL,
            category TEXT,
            version_timestamp INTEGER NOT NULL,
            size INTEGER,
            downloadable INTEGER DEFAULT 0,
            local_path TEXT,
            added_in_version INTEGER,
            removed_in_version INTEGER,
            FOREIGN KEY (version_timestamp) REFERENCES versions(timestamp),
            UNIQUE(hash, version_timestamp)
        );
        CREATE INDEX IF NOT EXISTS idx_bundles_name ON bundles(name);
        CREATE INDEX IF NOT EXISTS idx_bundles_version ON bundles(version_timestamp);
        CREATE INDEX IF NOT EXISTS idx_versions_ts ON versions(timestamp);
        CREATE INDEX IF NOT EXISTS idx_bundles_name_ver ON bundles(name, ver);
        CREATE INDEX IF NOT EXISTS idx_sub_hash ON sub_bundles(hash);
        CREATE INDEX IF NOT EXISTS idx_sub_ver ON sub_bundles(version_timestamp);
    """)
    conn.commit()
    conn.close()
    logger.info(f"数据库初始化完成: {path}")
    return path


def get_conn():
    return sqlite3.connect(DB_PATH)


def save_version(timestamp, updateinfo, versions_data, is_current=True):
    conn = get_conn()
    if is_current:
        conn.execute("UPDATE versions SET is_current=0")
    conn.execute("""
        INSERT OR REPLACE INTO versions
        (timestamp, arts_ver, data_ver, other_ver, video_ver,
         apk_version, manifest_file, updateinfo_json, versions_json, is_current)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
    """, (
        timestamp,
        _extract_ver(versions_data, "Arts"),
        _extract_ver(versions_data, "Data"),
        _extract_ver(versions_data, "Other"),
        _extract_ver(versions_data, "Video"),
        updateinfo.get("latestVersion", ""),
        updateinfo.get("file", ""),
        json.dumps(updateinfo, ensure_ascii=False),
        json.dumps(versions_data, ensure_ascii=False),
        1 if is_current else 0
    ))
    for item in versions_data.get("data", []):
        conn.execute("""
            INSERT OR REPLACE INTO bundles (name, hash, size, ver, version_timestamp)
            VALUES (?, ?, ?, ?, ?)
        """, (item["name"], item["hash"], item["size"], item["ver"], timestamp))
    conn.commit()
    conn.close()
    logger.info(f"保存版本 {timestamp}，{len(versions_data.get('data', []))} 个分类包")


def _extract_ver(data, name):
    for item in data.get("data", []):
        if item["name"] == name:
            return item["ver"]
    return 0


def get_all_versions():
    conn = get_conn()
    rows = conn.execute("""
        SELECT timestamp, arts_ver, data_ver, other_ver, video_ver,
               apk_version, manifest_file, is_current, downloaded,
               created_at, notes
        FROM versions ORDER BY timestamp DESC
    """).fetchall()
    conn.close()
    return rows


def get_current_version():
    conn = get_conn()
    row = conn.execute("""
        SELECT timestamp, arts_ver, data_ver, other_ver, video_ver,
               apk_version, manifest_file, updateinfo_json, versions_json, notes
        FROM versions WHERE is_current=1 ORDER BY timestamp DESC LIMIT 1
    """).fetchone()
    conn.close()
    return row


def get_version(timestamp):
    conn = get_conn()
    row = conn.execute("""
        SELECT timestamp, arts_ver, data_ver, other_ver, video_ver,
               apk_version, manifest_file, updateinfo_json, versions_json, notes
        FROM versions WHERE timestamp=?
    """, (timestamp,)).fetchone()
    conn.close()
    return row


def get_bundles_for_version(timestamp, name_filter=None, limit=None, offset=None):
    conn = get_conn()
    sql = "SELECT name, hash, size, ver, local_path FROM bundles WHERE version_timestamp=?"
    params = [timestamp]
    if name_filter:
        sql += " AND name LIKE ?"
        params.append(f"%{name_filter}%")
    sql += " ORDER BY name"
    if limit is not None:
        sql += " LIMIT ?"
        params.append(limit)
        if offset is not None:
            sql += " OFFSET ?"
            params.append(offset)
    rows = conn.execute(sql, params).fetchall()
    conn.close()
    return rows


def get_bundle_count(timestamp):
    conn = get_conn()
    row = conn.execute(
        "SELECT COUNT(*) FROM bundles WHERE version_timestamp=?", (timestamp,)
    ).fetchone()
    conn.close()
    return row[0] if row else 0


def get_bundle_history(name):
    conn = get_conn()
    rows = conn.execute("""
        SELECT b.ver, b.hash, b.size, b.version_timestamp, v.created_at
        FROM bundles b
        LEFT JOIN versions v ON b.version_timestamp = v.timestamp
        WHERE b.name = ?
        ORDER BY b.ver DESC
    """, (name,)).fetchall()
    conn.close()
    return rows


def record_changes(from_ts, to_ts):
    conn = get_conn()
    old_bundles = {r[0]: (r[1], r[2]) for r in conn.execute(
        "SELECT name, ver, hash FROM bundles WHERE version_timestamp=?", (from_ts,)
    ).fetchall()}
    new_bundles = {r[0]: (r[1], r[2], r[3]) for r in conn.execute(
        "SELECT name, ver, hash, size FROM bundles WHERE version_timestamp=?", (to_ts,)
    ).fetchall()}
    conn.execute(
        "DELETE FROM version_changes WHERE from_timestamp=? AND to_timestamp=?",
        (from_ts, to_ts)
    )
    for name, (new_ver, new_hash, new_size) in new_bundles.items():
        if name in old_bundles:
            old_ver, old_hash = old_bundles[name]
            if old_ver != new_ver:
                conn.execute("""
                    INSERT INTO version_changes
                    (from_timestamp, to_timestamp, bundle_name, from_ver, to_ver, from_hash, to_hash, size_diff)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """, (from_ts, to_ts, name, old_ver, new_ver, old_hash, new_hash, new_size))
        else:
            conn.execute("""
                INSERT INTO version_changes
                (from_timestamp, to_timestamp, bundle_name, from_ver, to_ver, from_hash, to_hash, size_diff)
                VALUES (?, ?, ?, 0, ?, '', ?, ?)
            """, (from_ts, to_ts, name, new_ver, new_hash, new_size))
    conn.commit()
    conn.close()
    logger.info(f"记录版本变更 {from_ts} -> {to_ts}，{len(new_bundles)} 个 bundle")


def get_version_changes(from_ts, to_ts):
    conn = get_conn()
    rows = conn.execute("""
        SELECT bundle_name, from_ver, to_ver, from_hash, to_hash, size_diff
        FROM version_changes
        WHERE from_timestamp=? AND to_timestamp=?
        ORDER BY bundle_name
    """, (from_ts, to_ts)).fetchall()
    conn.close()
    return rows


def update_bundle_path(name, ver, path):
    conn = get_conn()
    conn.execute(
        "UPDATE bundles SET local_path=? WHERE name=? AND ver=?",
        (path, name, ver)
    )
    conn.commit()
    conn.close()


def set_downloaded(timestamp):
    conn = get_conn()
    conn.execute("UPDATE versions SET downloaded=1 WHERE timestamp=?", (timestamp,))
    conn.commit()
    conn.close()


def add_notes(timestamp, notes):
    conn = get_conn()
    conn.execute("UPDATE versions SET notes=? WHERE timestamp=?", (notes, timestamp))
    conn.commit()
    conn.close()


def save_sub_bundles(version_ts, hashes):
    conn = get_conn()
    for h in hashes:
        conn.execute("""
            INSERT OR REPLACE INTO sub_bundles (hash, version_timestamp, downloadable)
            VALUES (?, ?, 1)
        """, (h, version_ts))
    conn.commit()
    conn.close()
    logger.info(f"保存版本 {version_ts} 的 {len(hashes)} 个 sub_bundle")


def get_sub_bundles(version_ts):
    conn = get_conn()
    rows = conn.execute(
        "SELECT hash, downloadable, local_path FROM sub_bundles WHERE version_timestamp=?",
        (version_ts,),
    ).fetchall()
    conn.close()
    return rows


def get_sub_bundle_count(version_ts):
    conn = get_conn()
    row = conn.execute(
        "SELECT COUNT(*) FROM sub_bundles WHERE version_timestamp=?", (version_ts,)
    ).fetchone()
    conn.close()
    return row[0] if row else 0


def save_delta(from_ts, to_ts, added, removed, common_count):
    conn = get_conn()
    conn.execute("DELETE FROM version_changes WHERE from_timestamp=? AND to_timestamp=?",
                 (from_ts, to_ts))
    conn.execute("""
        INSERT INTO version_changes
        (from_timestamp, to_timestamp, bundle_name, from_ver, to_ver, from_hash, to_hash, size_diff)
        VALUES (?, ?, ?, 0, 0, '', '', ?)
    """, (from_ts, to_ts, f"[SUMMARY] added={len(added)} removed={len(removed)} common={common_count}", 0))
    conn.commit()
    conn.close()
    logger.info(f"保存 delta {from_ts} -> {to_ts}：新增 {len(added)}，移除 {len(removed)}，未变 {common_count}")


def get_stats():
    conn = get_conn()
    version_count = conn.execute("SELECT COUNT(*) FROM versions").fetchone()[0]
    bundle_count = conn.execute("SELECT COUNT(*) FROM bundles").fetchone()[0]
    total_size = conn.execute(
        "SELECT COALESCE(SUM(size),0) FROM bundles b INNER JOIN versions v ON b.version_timestamp=v.timestamp WHERE v.is_current=1"
    ).fetchone()[0]
    conn.close()
    return {"versions": version_count, "bundles": bundle_count, "total_size": total_size}
