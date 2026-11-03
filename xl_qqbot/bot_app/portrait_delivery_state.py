"""Bot-owned immutable delivery intents. No server database writes."""

import hashlib
import json
import sqlite3
from pathlib import Path


def encode(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def digest(value):
    return hashlib.sha256(encode(value).encode()).hexdigest()


class DeliveryState:
    def __init__(self, path, scope):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.db = sqlite3.connect(self.path)
        self.db.execute("PRAGMA synchronous=FULL")
        self.db.executescript("""
            CREATE TABLE IF NOT EXISTS meta (key TEXT PRIMARY KEY, value TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS generations (run TEXT PRIMARY KEY, value TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS intents (run TEXT, name TEXT, envelope TEXT NOT NULL,
                accepted INTEGER NOT NULL DEFAULT 0, PRIMARY KEY(run,name));
            CREATE TABLE IF NOT EXISTS chunks (run TEXT, name TEXT, ordinal INTEGER,
                envelope TEXT NOT NULL, accepted INTEGER NOT NULL DEFAULT 0,
                PRIMARY KEY(run,name,ordinal));
            CREATE TABLE IF NOT EXISTS quarantines (key TEXT PRIMARY KEY, value TEXT NOT NULL);
        """)
        existing = self.get("scope")
        if existing is not None and existing != scope:
            self.db.close()
            raise ValueError(
                "delivery scope/configuration mismatch; reconciliation required"
            )
        self.scope = scope
        self.put("scope", scope)
        if self.get("schema") not in (None, 1):
            raise ValueError("delivery schema mismatch")
        self.put("schema", 1)

    def close(self):
        self.db.close()

    def get(self, key, default=None):
        row = self.db.execute("SELECT value FROM meta WHERE key=?", (key,)).fetchone()
        return json.loads(row[0]) if row else default

    def put(self, key, value):
        with self.db:
            self.db.execute(
                "INSERT OR REPLACE INTO meta VALUES (?,?)", (key, encode(value))
            )

    def generation(self, run):
        row = self.db.execute(
            "SELECT value FROM generations WHERE run=?", (run,)
        ).fetchone()
        return json.loads(row[0]) if row else None

    def freeze_generation(self, run, version, recipients, eligible):
        value = {
            "run": run,
            "version": version,
            "recipients": list(recipients),
            "eligible": eligible,
            "terminal": None,
            "barrier": None,
            "done": False,
        }
        with self.db:
            self.db.execute(
                "INSERT OR IGNORE INTO generations VALUES (?,?)", (run, encode(value))
            )
        return self.generation(run)

    def update_generation(self, run, **changes):
        value = self.generation(run)
        value.update(changes)
        with self.db:
            self.db.execute(
                "UPDATE generations SET value=? WHERE run=?", (encode(value), run)
            )
        return value

    def pending(self, after="", limit=16):
        rows = self.db.execute(
            "SELECT run,value FROM generations WHERE run>? AND json_extract(value, '$.done')=0 ORDER BY run LIMIT ?",
            (after, limit),
        ).fetchall()
        return [(run, json.loads(raw)) for run, raw in rows]

    def freeze_intent(self, run, name, items):
        prior = self.db.execute(
            "SELECT envelope FROM intents WHERE run=? AND name=?", (run, name)
        ).fetchone()
        if prior:
            return json.loads(prior[0])
        gen = self.generation(run)
        messages = [
            dict(item, recipient=recipient, ordinal=i)
            for i, item in enumerate(items)
            for recipient in gen["recipients"]
        ]
        envelope = {
            "event_key": "portrait-delivery:"
            + digest([self.scope, gen.get("core_identity"), run, name]),
            "instance_id": self.scope["instance"],
            "messages": messages,
        }
        with self.db:
            self.db.execute(
                "INSERT INTO intents VALUES (?,?,?,?)",
                (run, name, encode(envelope), int(not messages)),
            )
            for ordinal, start in enumerate(range(0, len(messages), 100)):
                chunk = dict(envelope, messages=messages[start : start + 100])
                self.db.execute(
                    "INSERT INTO chunks VALUES (?,?,?,?,0)",
                    (run, name, ordinal, encode(chunk)),
                )
        return envelope

    def accepted(self, run, name):
        row = self.db.execute(
            "SELECT accepted FROM intents WHERE run=? AND name=?", (run, name)
        ).fetchone()
        return bool(row and row[0])

    async def submit(self, run, name, sink):
        for ordinal, raw in self.db.execute(
            "SELECT ordinal,envelope FROM chunks WHERE run=? AND name=? AND accepted=0 ORDER BY ordinal",
            (run, name),
        ).fetchall():
            await sink.enqueue(**json.loads(raw))
            with self.db:
                self.db.execute(
                    "UPDATE chunks SET accepted=1 WHERE run=? AND name=? AND ordinal=?",
                    (run, name, ordinal),
                )
        with self.db:
            self.db.execute(
                "UPDATE intents SET accepted=1 WHERE run=? AND name=?", (run, name)
            )

    def quarantine(self, key, value):
        with self.db:
            self.db.execute(
                "INSERT OR IGNORE INTO quarantines VALUES (?,?)", (key, encode(value))
            )
            if key.startswith("line:"):
                self.db.execute(
                    "DELETE FROM quarantines WHERE key LIKE 'line:%' AND rowid NOT IN (SELECT rowid FROM quarantines WHERE key LIKE 'line:%' ORDER BY rowid DESC LIMIT 100)"
                )

    def health(self):
        return {
            "cursors": {
                k: json.loads(v)
                for k, v in self.db.execute(
                    "SELECT key,value FROM meta WHERE key LIKE 'cursor:%'"
                )
            },
            "quarantines": [
                json.loads(v)
                for (v,) in self.db.execute(
                    "SELECT value FROM quarantines ORDER BY key LIMIT 100"
                )
            ],
            "error": self.get("health_error"),
            "unresolved": [
                {
                    "run": run,
                    "terminal": bool(value["terminal"]),
                    "barrier": bool(value["barrier"]),
                    "done": value["done"],
                    "problems": value.get("problems", {}),
                }
                for run, value in self.pending(limit=16)
            ],
            "pending_chunks": self.db.execute(
                "SELECT count(*) FROM chunks WHERE accepted=0"
            ).fetchone()[0],
        }
