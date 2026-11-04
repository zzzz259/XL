"""Read-only bounded runtime evidence. Wires do not grant core authority."""

import hashlib
import json
import math
import sqlite3
import uuid
from contextlib import contextmanager
from pathlib import Path

MAX_RECORD = 1024 * 1024
MAX_DOCUMENT = 16 * MAX_RECORD
MAX_EVENTS = 65536
SCHEMA = (
    "CREATE TABLE identity(schema INTEGER PRIMARY KEY CHECK(schema=1), instance_id TEXT NOT NULL, source_id TEXT NOT NULL, database_id TEXT NOT NULL)",
    "CREATE TABLE runs(key TEXT PRIMARY KEY, payload TEXT NOT NULL, digest TEXT NOT NULL)",
    "CREATE TABLE documents(digest TEXT PRIMARY KEY, payload TEXT NOT NULL)",
    "CREATE TABLE cursors(stream TEXT PRIMARY KEY, payload TEXT NOT NULL)",
    "CREATE TABLE history(sequence INTEGER PRIMARY KEY, key TEXT NOT NULL, digest TEXT NOT NULL, prefix TEXT NOT NULL)",
)


def encode(value, cap=MAX_RECORD):
    raw = json.dumps(
        value,
        sort_keys=True,
        ensure_ascii=False,
        separators=(",", ":"),
        allow_nan=False,
    )
    if len(raw.encode()) > cap:
        raise ValueError("runtime record exceeds bound")
    return raw


def sha(value):
    return hashlib.sha256(value.encode()).hexdigest()


def text(value):
    if (
        type(value) is not str
        or not value.strip()
        or len(value) > 256
        or any(ord(c) < 32 for c in value)
    ):
        raise ValueError("invalid runtime identity")
    return value


def number(value):
    if type(value) not in (int, float) or not math.isfinite(value) or value < 0:
        raise ValueError("invalid runtime time")
    return value


def hash_value(value):
    if (
        type(value) is not str
        or len(value) != 64
        or any(c not in "0123456789abcdef" for c in value)
    ):
        raise ValueError("invalid runtime hash")
    return value


def core(value):
    if type(value) is not dict or set(value) != {"scope", "journal_id", "file_id"}:
        raise ValueError("invalid core identity")
    hash_value(value["scope"])
    if str(uuid.UUID(value["journal_id"])) != value["journal_id"]:
        raise ValueError("invalid core journal identity")
    if (
        type(value["file_id"]) is not list
        or len(value["file_id"]) != 2
        or any(type(v) is not int or v < 0 for v in value["file_id"])
        or value["file_id"][1] == 0
    ):
        raise ValueError("invalid core file identity")
    return value


def run_key(identity, run_id):
    return sha(encode([core(identity), text(run_id)]))


def file_id(path):
    info = path.stat()
    if not info.st_ino:
        raise ValueError("missing runtime file incarnation")
    return [info.st_dev, info.st_ino]


def path_check(path):
    path = Path(path)
    if not path.is_absolute() or ".." in path.parts:
        raise ValueError("runtime path must be explicit absolute")
    for part in (path, *path.parents):
        if part.is_symlink():
            raise ValueError("runtime path symlink")
    return path


def check(db, instance_id, source_id, database_id=None):
    actual = db.execute(
        "SELECT sql FROM sqlite_master WHERE type='table' ORDER BY name"
    ).fetchall()
    if sorted(row[0] for row in actual) != sorted(SCHEMA):
        raise ValueError("runtime schema mismatch")
    rows = db.execute("SELECT * FROM identity").fetchall()
    if len(rows) != 1 or rows[0][:3] != (1, instance_id, source_id):
        raise ValueError("runtime scope mismatch")
    if str(uuid.UUID(rows[0][3])) != rows[0][3] or (
        database_id is not None and rows[0][3] != database_id
    ):
        raise ValueError("runtime identity mismatch")
    return rows[0][3]


class RuntimeLedgerReader:
    """Existing ledger only; no migrations, repair, media or orchestration imports."""

    def __init__(self, path, instance_id, source_id):
        self.path = path_check(path)
        self.instance_id, self.source_id = text(instance_id), text(source_id)
        self.incarnation = file_id(self.path)
        self.database_id = None
        self._anchor = (0, "0" * 64)
        with self._connection() as db:
            self.database_id = check(db, instance_id, source_id)

    def _continuity(self, db):
        previous = "0" * 64
        high = 0
        anchor_seen = self._anchor[0] == 0
        rows = db.execute(
            "SELECT sequence,key,digest,prefix FROM history ORDER BY sequence LIMIT ?",
            (MAX_EVENTS + 1,),
        )
        for seq, key, digest, prefix in rows:
            if (
                seq != high + 1
                or seq > MAX_EVENTS
                or prefix != sha(encode([previous, seq, key, digest]))
            ):
                raise ValueError("runtime history continuity mismatch")
            if seq == self._anchor[0]:
                if prefix != self._anchor[1]:
                    raise ValueError("runtime history restored")
                anchor_seen = True
            high, previous = seq, prefix
        if not anchor_seen:
            raise ValueError("runtime history rolled back")
        return high, previous

    @contextmanager
    def _connection(self):
        path_check(self.path)
        if file_id(self.path) != self.incarnation:
            raise ValueError("runtime file replaced")
        db = sqlite3.connect(self.path.as_uri() + "?mode=ro", uri=True, timeout=1)
        try:
            db.execute("PRAGMA query_only=ON")
            db.execute("BEGIN")
            check(db, self.instance_id, self.source_id, self.database_id)
            anchor = self._continuity(db)
            yield db
            if file_id(self.path) != self.incarnation:
                raise ValueError("runtime file replaced")
            self._anchor = anchor
        finally:
            db.close()

    @staticmethod
    def _read(db, key):
        row = db.execute(
            "SELECT substr(payload,1,?),digest FROM runs WHERE key=?",
            (MAX_RECORD + 1, key),
        ).fetchone()
        if row is None:
            if db.execute(
                "SELECT 1 FROM history WHERE key=? LIMIT 1", (key,)
            ).fetchone():
                raise ValueError("runtime run missing despite retained history")
            raise KeyError(key)
        raw, digest = row
        latest = db.execute(
            "SELECT digest FROM history WHERE key=? ORDER BY sequence DESC LIMIT 1",
            (key,),
        ).fetchone()
        if sha(raw) != digest or latest != (digest,):
            raise ValueError("runtime run digest mismatch")
        value = json.loads(raw)
        if (
            encode(value) != raw
            or run_key(value["core_identity"], value["run_id"]) != key
        ):
            raise ValueError("runtime run key mismatch")
        return value

    def read_run_mapping(self, core_identity, run_id):
        with self._connection() as db:
            return self._read(db, run_key(core_identity, run_id))

    @staticmethod
    def _read_cursor(db, stream):
        row = db.execute(
            "SELECT substr(payload,1,?) FROM cursors WHERE stream=?",
            (MAX_RECORD + 1, stream),
        ).fetchone()
        latest = db.execute(
            "SELECT digest FROM history WHERE key=? ORDER BY sequence DESC LIMIT 1",
            ("cursor:" + stream,),
        ).fetchone()
        if row is None:
            if latest is not None:
                raise ValueError("runtime cursor missing despite retained history")
            return None
        value = json.loads(row[0])
        if encode(value) != row[0] or latest != (sha(row[0]),):
            raise ValueError("runtime cursor digest mismatch")
        return value

    def read_cursor(self, stream):
        if stream not in ("outcomes", "source_links"):
            raise ValueError("invalid core stream")
        with self._connection() as db:
            return self._read_cursor(db, stream)

    def read_intent(self, core_identity, run_id):
        with self._connection() as db:
            mapping = self._read(db, run_key(core_identity, run_id))
            result = {}
            for name, digest in mapping["documents"].items():
                row = db.execute(
                    "SELECT substr(payload,1,?) FROM documents WHERE digest=?",
                    (MAX_DOCUMENT + 1, digest),
                ).fetchone()
                if row is None or sha(row[0]) != digest:
                    raise ValueError("runtime immutable document missing/corrupt")
                value = json.loads(row[0])
                if encode(value, MAX_DOCUMENT) != row[0]:
                    raise ValueError("runtime immutable document invalid")
                result[name] = value
            return result

    def health(self):
        with self._connection() as db:
            return {
                "enabled": True,
                "database_id": self.database_id,
                "runs": db.execute("SELECT count(*) FROM runs").fetchone()[0],
                "transitions": self._anchor[0],
            }

    def read_fence(self):
        """Exact existing ledger identity and history high-water, without mutation."""
        with self._connection() as db:
            high, prefix = self._continuity(db)
            return {
                "database_id": self.database_id,
                "file_id": self.incarnation,
                "sequence": high,
                "prefix": prefix,
            }

    def _validate_frontier(self, db, cursor):
        if (
            type(cursor) is not dict
            or set(cursor) != {"database_id", "file_id", "sequence", "prefix"}
            or cursor["database_id"] != self.database_id
            or cursor["file_id"] != self.incarnation
            or type(cursor["sequence"]) is not int
            or not 0 <= cursor["sequence"] <= MAX_EVENTS
        ):
            raise ValueError("invalid runtime frontier")
        row = (
            db.execute(
                "SELECT prefix FROM history WHERE sequence=?", (cursor["sequence"],)
            ).fetchone()
            if cursor["sequence"]
            else ("0" * 64,)
        )
        if row != (cursor["prefix"],):
            raise ValueError("runtime frontier continuity mismatch")

    def read_changes_page(self, *, cursor=None, limit=16, max_bytes=MAX_RECORD):
        """Bounded history delta; every returned mapping is its authenticated current state."""
        if type(limit) is not int or not 1 <= limit <= 16:
            raise ValueError("invalid runtime page limit")
        if type(max_bytes) is not int or not 1 <= max_bytes <= MAX_DOCUMENT:
            raise ValueError("invalid runtime page byte bound")
        with self._connection() as db:
            high, _ = self._continuity(db)
            after, prefix = 0, "0" * 64
            if cursor is not None:
                self._validate_frontier(db, cursor)
                after, prefix = cursor["sequence"], cursor["prefix"]
            items, size = {}, 0
            for sequence, key, next_prefix in db.execute(
                "SELECT sequence,key,prefix FROM history WHERE sequence>? ORDER BY sequence LIMIT ?",
                (after, limit),
            ):
                if not key.startswith("cursor:") and key not in items:
                    value = self._read(db, key)
                    length = len(encode(value).encode())
                    if size + length > max_bytes:
                        if not items:
                            raise ValueError("first runtime mapping exceeds page bound")
                        break
                    items[key] = value
                    size += length
                after, prefix = sequence, next_prefix
            return {
                "items": list(items.values()),
                "cursor": {
                    "database_id": self.database_id,
                    "file_id": self.incarnation,
                    "sequence": after,
                    "prefix": prefix,
                },
                "caught_up": after == high,
            }

    def read_runs_page(
        self, *, cursor=None, limit=16, max_bytes=MAX_DOCUMENT, live=False
    ):
        """Primary-key pagination over one stable ledger revision; no phase scan."""
        if type(limit) is not int or not 1 <= limit <= 16:
            raise ValueError("invalid runtime page limit")
        if type(max_bytes) is not int or not 1 <= max_bytes <= MAX_DOCUMENT:
            raise ValueError("invalid runtime page byte bound")
        if type(live) is not bool:
            raise ValueError("invalid runtime live scan")
        with self._connection() as db:
            high, prefix = self._continuity(db)
            fence = {
                "database_id": self.database_id,
                "file_id": self.incarnation,
                "sequence": high,
                "prefix": prefix,
            }
            after = ""
            if cursor is not None:
                if (
                    type(cursor) is not dict
                    or set(cursor) != {"fence", "after"}
                    or (not live and cursor["fence"] != fence)
                ):
                    raise ValueError("runtime scan fence changed")
                if live:
                    self._validate_frontier(db, cursor["fence"])
                after = cursor["after"]
                if after != "":
                    hash_value(after)
                    self._read(db, after)
            items, size = [], 0
            keys = db.execute(
                "SELECT key FROM runs WHERE key>? ORDER BY key LIMIT ?", (after, limit)
            ).fetchall()
            for (key,) in keys:
                value = self._read(db, key)
                length = len(encode(value).encode())
                if size + length > max_bytes:
                    if not items:
                        raise ValueError("first runtime mapping exceeds page bound")
                    break
                items.append(value)
                size += length
                after = key
            return {"items": items, "cursor": {"fence": fence, "after": after}}
