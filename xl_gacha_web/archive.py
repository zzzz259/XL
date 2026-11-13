"""Local, transactional multi-account history. Never stores arbitrary input fields."""

from __future__ import annotations

import csv
import hashlib
import io
import json
import re
import sqlite3
import threading
import uuid
from collections import Counter
from contextlib import contextmanager
from datetime import datetime
from pathlib import Path

from gacha_core.analysis import ANALYSIS_VERSION, analyze_history
from gacha_core.gacha_report import CHINA

MAX_FILE = 30 * 1024 * 1024


class ArchiveError(Exception):
    pass


def text(value, limit=120):
    if (
        not isinstance(value, str)
        or len(value) > limit
        or any(ord(c) < 32 for c in value)
    ):
        raise ArchiveError("INVALID_ARCHIVE")
    return value.strip()


def number(value, minimum=0, maximum=2**53 - 1):
    if isinstance(value, bool):
        raise ArchiveError("INVALID_ARCHIVE")
    try:
        n = int(value)
    except (ValueError, TypeError, OverflowError):
        raise ArchiveError("INVALID_ARCHIVE") from None
    if str(n) != str(value) or not minimum <= n <= maximum:
        raise ArchiveError("INVALID_ARCHIVE")
    return n


def account_id(uid, channel):
    return "account-" + hashlib.sha256(f"{channel}:{uid}".encode()).hexdigest()[:32]


def summarize(records):
    stars, pools = Counter(), Counter()
    for r in records:
        for d in r["drops"]:
            stars[str(d["rarity"]) if d["rarity"] is not None else "unknown"] += d[
                "count"
            ]
            pools[r["pool_name"]] += d["count"]
    return {
        "record_count": len(records),
        "pull_count": sum(stars.values()),
        "rarity_counts": dict(stars),
        "pool_pull_counts": dict(pools),
        "unknown_item_count": stars.get("unknown", 0),
    }


class Archive:
    def __init__(self, directory, catalog):
        self.directory = Path(directory).resolve()
        self.directory.mkdir(parents=True, exist_ok=True)
        self.path = self.directory / "history.sqlite3"
        self.catalog = catalog
        self.catalog_lock = threading.RLock()
        with self.connect() as db:
            db.execute(
                "CREATE TABLE IF NOT EXISTS accounts (id TEXT PRIMARY KEY, report TEXT NOT NULL)"
            )
            db.execute(
                "CREATE TABLE IF NOT EXISTS preferences (key TEXT PRIMARY KEY, value TEXT NOT NULL)"
            )

    @contextmanager
    def connect(self):
        # A fresh connection per operation; context commits/rolls back all accounts together.
        with self.catalog_lock:
            db = sqlite3.connect(self.path, timeout=20)
            db.execute("PRAGMA secure_delete=ON")
            try:
                with db:
                    db.execute("BEGIN IMMEDIATE")
                    yield db
            finally:
                db.close()

    def use_catalog(self, catalog):
        with self.catalog_lock:
            self.catalog = catalog

    def _load(self, db, key):
        row = db.execute("SELECT report FROM accounts WHERE id=?", (key,)).fetchone()
        if not row:
            raise ArchiveError("ACCOUNT_NOT_FOUND")
        report = json.loads(row[0])
        if report.get("analysis", {}).get("version") != ANALYSIS_VERSION or report.get(
            "configuration_revision"
        ) != getattr(self.catalog, "fingerprint", self.catalog.version):
            records, metadata = self._normalize(report)
            report["records"] = records
            report["summary"] = summarize(records)
            report["configuration_version"] = self.catalog.version
            report["configuration_revision"] = getattr(
                self.catalog, "fingerprint", self.catalog.version
            )
            report["pool_metadata"] = metadata
            report["analysis"] = analyze_history(
                records, {**self.catalog.pool_meta, **metadata}
            )
            db.execute(
                "UPDATE accounts SET report=? WHERE id=?",
                (json.dumps(report, ensure_ascii=False), key),
            )
        return report

    def list_accounts(self):
        with self.connect() as db:
            reports = [
                json.loads(row[0]) for row in db.execute("SELECT report FROM accounts")
            ]
        return sorted(
            [
                dict(
                    **r["account"],
                    pull_count=r["summary"]["pull_count"],
                    updated_at=r["captured_at"],
                )
                for r in reports
            ],
            key=lambda r: r["updated_at"],
            reverse=True,
        )

    def open(self, key):
        with self.connect() as db:
            report = self._load(db, key)
            db.execute(
                "INSERT OR REPLACE INTO preferences VALUES (?,?)", ("last_account", key)
            )
            return report

    def last_account(self):
        with self.connect() as db:
            row = db.execute(
                "SELECT value FROM preferences WHERE key=?", ("last_account",)
            ).fetchone()
            return row[0] if row else None

    def rename(self, key, label):
        label = text(label, 60)
        if not label:
            raise ArchiveError("INVALID_LABEL")
        with self.connect() as db:
            report = self._load(db, key)
            report["account"]["label"] = label
            db.execute(
                "UPDATE accounts SET report=? WHERE id=?",
                (json.dumps(report, ensure_ascii=False), key),
            )
        return report

    def delete(self, key):
        with self.connect() as db:
            self._load(db, key)
            db.execute("DELETE FROM accounts WHERE id=?", (key,))
            db.execute(
                "DELETE FROM preferences WHERE key=? AND value=?", ("last_account", key)
            )

    def backup(self):
        with self.connect() as db:
            return {
                "format": "starfall-local-backup",
                "version": 1,
                "exported_at": datetime.now(CHINA).isoformat(),
                "accounts": [
                    self._load(db, row[0])
                    for row in db.execute(
                        "SELECT id FROM accounts ORDER BY id"
                    ).fetchall()
                ],
            }

    def _identity(self, value, label=""):
        value = value if isinstance(value, dict) else {}
        uid = text(str(value.get("uid", "")), 64)
        channel = text(value.get("channel", ""), 64)
        if uid or channel:
            if not uid or not re.fullmatch(r"[A-Za-z0-9_-]{1,64}", channel):
                raise ArchiveError("INVALID_ARCHIVE")
            key = account_id(uid, channel)
            if value.get("id", key) != key:
                raise ArchiveError("ACCOUNT_ID_MISMATCH")
        else:
            key = value.get("id") or "local-" + uuid.uuid4().hex
            if not isinstance(key, str) or not re.fullmatch(r"local-[a-f0-9]{32}", key):
                raise ArchiveError("INVALID_ARCHIVE")
        return {
            "id": key,
            "label": text(
                label
                or value.get("label")
                or (f"{channel} · UID {uid}" if uid else "导入的账号"),
                60,
            ),
            "uid": uid,
            "channel": channel,
        }

    def _normalize(self, source):
        if (
            not isinstance(source, dict)
            or not isinstance(source.get("records"), list)
            or len(source["records"]) > 100000
        ):
            raise ArchiveError("INVALID_ARCHIVE")
        result = []
        drop_total = 0
        for r in source["records"]:
            if (
                not isinstance(r, dict)
                or not isinstance(r.get("drops"), list)
                or not 1 <= len(r["drops"]) <= 1000
            ):
                raise ArchiveError("INVALID_ARCHIVE")
            pool = number(r.get("pool_id"), -(2**52))
            stamp = number(r.get("timestamp"), 0, 4102444800)
            drops = []
            for d in r["drops"]:
                if not isinstance(d, dict):
                    raise ArchiveError("INVALID_ARCHIVE")
                item = number(d.get("item_id"))
                name, rarity = self.catalog.cards.get(
                    item, (d.get("name", f"未知物品({item})"), d.get("rarity"))
                )
                rarity = number(rarity, 1, 5) if rarity not in (None, "") else None
                drops.append(
                    {
                        "item_id": item,
                        "name": text(name),
                        "rarity": rarity,
                        "count": number(d.get("count"), 1, 10000),
                    }
                )
                drop_total += drops[-1]["count"]
            if drop_total > 1000000:
                raise ArchiveError("ARCHIVE_TOO_LARGE")
            result.append(
                {
                    "pool_id": pool,
                    "pool_name": text(
                        self.catalog.pools.get(
                            pool, r.get("pool_name", f"未知卡池({pool})")
                        )
                    ),
                    "timestamp": stamp,
                    "time_beijing": datetime.fromtimestamp(stamp, CHINA).strftime(
                        "%Y-%m-%d %H:%M:%S"
                    ),
                    "index": number(r.get("index", 0)),
                    "record_id": number(r.get("record_id", 0)),
                    "drops": drops,
                }
            )
        metadata = {}
        raw_meta = source.get("pool_metadata", {})
        if not isinstance(raw_meta, dict):
            raise ArchiveError("INVALID_ARCHIVE")
        for r in result:
            k = str(r["pool_id"])
            if k in self.catalog.pool_meta:
                continue
            m = raw_meta.get(k, {})
            if not isinstance(m, dict):
                raise ArchiveError("INVALID_ARCHIVE")
            category = m.get("category", "unknown")
            if category not in (
                "normal",
                "limited",
                "collection",
                "beginner",
                "unknown",
            ):
                raise ArchiveError("INVALID_ARCHIVE")
            inferred = [
                self.catalog.pool_meta.get(str(pool), {})
                for pool, name in self.catalog.pools.items()
                if name == r["pool_name"]
            ]
            # Repeated banner names cannot identify one UP roster, but matching
            # configuration entries can still agree on a shared pity group.
            groups = {v.get("pity_group") for v in inferred}
            verified = (
                bool(inferred)
                and len(groups) == 1
                and None not in groups
                and all(v.get("category") == category for v in inferred)
            )
            metadata[k] = {
                "category": category,
                "pity_group": next(iter(groups)) if verified else f"import:{k}",
                "featured_ids": [],
                "featured_names": [],
                "hard_pity": 70 if category != "unknown" else None,
            }
        return result, metadata

    def _merge(self, db, source, identity):
        records, metadata = self._normalize(source)
        key = identity["id"]
        row = db.execute("SELECT report FROM accounts WHERE id=?", (key,)).fetchone()
        if row:
            old = self._load(db, key)
            identity = old["account"]
            records = old["records"] + records
            metadata = {**old.get("pool_metadata", {}), **metadata}
        unique = {}
        for r in records:
            stable = (r["pool_id"], r["timestamp"], r["index"], r["record_id"])
            # With no sequence identifiers, content is the only available discriminator.
            if not r["index"] and not r["record_id"]:
                stable += (json.dumps(r["drops"], sort_keys=True),)
            if stable in unique and unique[stable]["drops"] != r["drops"]:
                raise ArchiveError("RECORD_CONFLICT")
            unique[stable] = r
        records = sorted(
            unique.values(),
            key=lambda r: (r["timestamp"], r["index"], r["record_id"], r["pool_id"]),
        )
        if (
            len(records) > 100000
            or sum(d["count"] for r in records for d in r["drops"]) > 1000000
        ):
            raise ArchiveError("ARCHIVE_TOO_LARGE")
        report = {
            "schema_version": 1,
            "captured_at": datetime.now(CHINA).isoformat(),
            "configuration_version": self.catalog.version,
            "configuration_revision": getattr(
                self.catalog, "fingerprint", self.catalog.version
            ),
            "account": identity,
            "storage": "local",
            "partial": bool(source.get("partial")),
            "coverage": "本机累计保存的可见历史，账号全部生涯及官方实时保底仍未确认。",
            "summary": summarize(records),
            "records": records,
            "queries": [],
            "pool_metadata": metadata,
            "analysis": analyze_history(
                records, {**self.catalog.pool_meta, **metadata}
            ),
        }
        db.execute(
            "INSERT OR REPLACE INTO accounts VALUES (?,?)",
            (key, json.dumps(report, ensure_ascii=False)),
        )
        db.execute(
            "INSERT OR REPLACE INTO preferences VALUES (?,?)", ("last_account", key)
        )
        return report

    def save_query(self, report, uid, channel, catalog=None):
        identity = self._identity({"uid": uid, "channel": channel})
        with self.connect() as db:
            previous = self.catalog
            try:
                if catalog is not None:
                    self.catalog = catalog
                return self._merge(db, report, identity)
            finally:
                self.catalog = previous

    def _csv(self, content):
        reader = csv.DictReader(io.StringIO(content.lstrip("\ufeff")))
        required = {
            "时间（北京时间）",
            "卡池",
            "记录序号",
            "记录ID",
            "抽次（该记录内）",
            "物品ID",
            "角色/物品",
            "初始星级",
            "数量",
        }
        if not required.issubset(reader.fieldnames or []):
            raise ArchiveError("INVALID_CSV")
        groups, metadata, identities = {}, {}, set()
        for rownum, row in enumerate(reader):
            if rownum >= 100000:
                raise ArchiveError("ARCHIVE_TOO_LARGE")
            label = text(row["卡池"])
            if row.get("卡池ID"):
                pool = number(row["卡池ID"], -(2**52))
                if row.get("卡池分类"):
                    metadata[str(pool)] = {"category": text(row["卡池分类"])}
            else:
                candidates = [
                    k for k, name in self.catalog.pools.items() if name == label
                ]
                pool = (
                    candidates[0]
                    if len(candidates) == 1
                    else -int(hashlib.sha256(label.encode()).hexdigest()[:12], 16)
                )
                if len(candidates) > 1:
                    categories = {
                        self.catalog.pool_meta.get(str(k), {}).get(
                            "category", "unknown"
                        )
                        for k in candidates
                    }
                    if len(categories) == 1:
                        metadata[str(pool)] = {"category": categories.pop()}
            stamp = int(
                datetime.strptime(row["时间（北京时间）"], "%Y-%m-%d %H:%M:%S")
                .replace(tzinfo=CHINA)
                .timestamp()
            )
            index, rid = number(row["记录序号"]), number(row["记录ID"])
            key = (pool, stamp, index, rid)
            group = groups.setdefault(
                key,
                {
                    "pool_id": pool,
                    "pool_name": label,
                    "timestamp": stamp,
                    "index": index,
                    "record_id": rid,
                    "drops": {},
                },
            )
            position = number(row["抽次（该记录内）"], 1, 1000)
            d = {
                "item_id": number(row["物品ID"]),
                "name": text(row["角色/物品"]),
                "rarity": number(row["初始星级"], 1, 5) if row["初始星级"] else None,
                "count": number(row["数量"], 1, 10000),
            }
            if position in group["drops"] and group["drops"][position] != d:
                raise ArchiveError("RECORD_CONFLICT")
            group["drops"][position] = d
            if row.get("存档ID"):
                identities.add(
                    (
                        row["存档ID"],
                        row.get("账号名称", ""),
                        row.get("账号UID", ""),
                        row.get("登录渠道", ""),
                    )
                )
        if not groups or len(identities) > 1:
            raise ArchiveError("INVALID_CSV")
        records = []
        for r in groups.values():
            if sorted(r["drops"]) != list(range(1, len(r["drops"]) + 1)):
                raise ArchiveError("INVALID_CSV")
            r["drops"] = [d for _, d in sorted(r["drops"].items())]
            records.append(r)
        result = {"records": records, "pool_metadata": metadata}
        if identities:
            key, label, uid, channel = identities.pop()
            result["account"] = {
                "id": key,
                "label": label,
                "uid": uid,
                "channel": channel,
            }
        return result

    def import_data(self, content, fmt, target=None, label=""):
        if not isinstance(content, str) or len(content.encode("utf-8")) > MAX_FILE:
            raise ArchiveError("ARCHIVE_TOO_LARGE")
        try:
            if fmt not in ("csv", "json"):
                raise ArchiveError("INVALID_ARCHIVE")
            source = (
                self._csv(content)
                if fmt == "csv"
                else json.loads(content.lstrip("\ufeff"))
            )
            if not isinstance(source, dict):
                raise ArchiveError("INVALID_ARCHIVE")
            bulk = source.get("format") == "starfall-local-backup"
            if bulk and (source.get("version") != 1 or target):
                raise ArchiveError("INVALID_ARCHIVE")
            sources = source.get("accounts") if bulk else [source]
            if not isinstance(sources, list) or not 1 <= len(sources) <= 500:
                raise ArchiveError("INVALID_ARCHIVE")
            with self.connect() as db:
                results = []
                for item in sources:
                    if not isinstance(item, dict):
                        raise ArchiveError("INVALID_ARCHIVE")
                    identity = self._identity(item.get("account"), label)
                    if target:
                        selected = self._load(db, target)["account"]
                        if item.get("account") and identity["id"] != target:
                            raise ArchiveError("ACCOUNT_ID_MISMATCH")
                        identity = selected
                    results.append(self._merge(db, item, identity))
                return results
        except ArchiveError:
            raise
        except (
            ValueError,
            TypeError,
            KeyError,
            AttributeError,
            OverflowError,
            RecursionError,
            csv.Error,
        ):
            raise ArchiveError("INVALID_ARCHIVE") from None
