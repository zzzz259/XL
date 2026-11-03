"""Portable real-SQLite fixtures. Only tests write server-format evidence."""

import json
import sqlite3
import uuid
from contextlib import closing

from server_app.portraits import core_outcomes as core
from server_app.portraits import runtime_ledger_reader as runtime


class Evidence:
    def __init__(self, root):
        self.root = root
        self.scope = "a" * 64
        self.core_path = root / "core.db"
        self.ledger_path = root / "ledger.db"
        with closing(sqlite3.connect(self.core_path)) as db, db:
            db.executescript(
                "CREATE TABLE identity(schema INTEGER,scope TEXT,journal_id TEXT); CREATE TABLE outcomes(sequence INTEGER PRIMARY KEY AUTOINCREMENT,run_id TEXT UNIQUE,promotion INTEGER UNIQUE,payload TEXT NOT NULL,prefix TEXT NOT NULL); CREATE TABLE source_links(sequence INTEGER PRIMARY KEY AUTOINCREMENT,run_id TEXT UNIQUE,payload TEXT NOT NULL,prefix TEXT NOT NULL);"
            )
            db.execute(
                "INSERT INTO identity VALUES (3,?,?)", (self.scope, str(uuid.uuid4()))
            )
        with closing(sqlite3.connect(self.ledger_path)) as db, db:
            for sql in runtime.SCHEMA:
                db.execute(sql)
            db.execute(
                "INSERT INTO identity VALUES (1,?,?,?)", ("i", "s", str(uuid.uuid4()))
            )

    def outcome(
        self, run="a", version="5", phase="published", eligible=True, cards=None
    ):
        value = {
            "run_id": run,
            "scope": self.scope,
            "trigger": "scheduled",
            "version": version,
            "classification": "processed",
            "announcement_eligible": eligible,
            "phase": phase,
            "source_state": "pending" if phase == "published" else "not_requested",
            "counts": {"new_character_count": 2},
            "warnings": [],
            "cards": cards or {"kind": "none", "reason": "no-new-output"},
            "pin_digest": cards["manifest"]["pin_digest"]
            if cards and cards["kind"] == "batch"
            else None,
            "code": None,
            "promotion": None,
            "source_recovery": None,
        }
        with closing(sqlite3.connect(self.core_path)) as db, db:
            seq = db.execute("SELECT count(*)+1 FROM outcomes").fetchone()[0]
            if phase == "published":
                value["promotion"] = seq
            core._validate(value, self.scope, run, "outcomes", 3)
            raw = core._encode(value)
            prior = db.execute(
                "SELECT prefix FROM outcomes ORDER BY sequence DESC LIMIT 1"
            ).fetchone()
            prefix = core._chain(prior[0] if prior else "0" * 64, seq, raw)
            db.execute(
                "INSERT INTO outcomes VALUES(?,?,?,?,?)",
                (seq, run, value["promotion"], raw, prefix),
            )
        return dict(value, sequence=seq)

    def mapping(self, value, documents=None):
        with closing(sqlite3.connect(self.ledger_path)) as db, db:
            value = dict(value, documents={})
            for name, doc in (documents or {}).items():
                raw = runtime.encode(doc)
                sha = runtime.sha(raw)
                value["documents"][name] = sha
                db.execute("INSERT OR IGNORE INTO documents VALUES (?,?)", (sha, raw))
            key = runtime.run_key(value["core_identity"], value["run_id"])
            raw = runtime.encode(value)
            sha = runtime.sha(raw)
            db.execute("INSERT OR REPLACE INTO runs VALUES(?,?,?)", (key, raw, sha))
            prior = db.execute(
                "SELECT sequence,prefix FROM history ORDER BY sequence DESC LIMIT 1"
            ).fetchone()
            seq = prior[0] + 1 if prior else 1
            prefix = prior[1] if prior else "0" * 64
            db.execute(
                "INSERT INTO history VALUES(?,?,?,?)",
                (seq, key, sha, runtime.sha(runtime.encode([prefix, seq, key, sha]))),
            )


def make_plan(identity, outcome, empty=False):
    from server_app.portraits.model import PROFILE, digest

    ref = {
        "schema": "portrait-prepared-v1",
        "instance_id": "i",
        "source_id": "s",
        "version": "5",
        "pin_digest": "a" * 64,
        "tool_fingerprint": "c" * 64,
        "snapshot_digest": "b" * 64,
        "capture": {
            "scope_key": digest(["i", "s"]),
            "pin_digest": "a" * 64,
            "locator": "capture.json",
            "size": 123,
            "sha256": "d" * 64,
        },
        "assigned": 0,
        "available": 0,
        "blocked": 0,
        "unassigned": 0,
    }
    job = {
        "schema_version": 2,
        "instance_id": "i",
        "source_id": "s",
        "version": "5",
        "character_id": "1",
        "skin_id": "2",
        "profile": PROFILE,
        "reason": "new_skin",
        "render_key": "a" * 64,
        "input_snapshot_digest": "b" * 64,
        "tool_fingerprint": "c" * 64,
        "source_capture_ref": ref,
    }
    job["job_id"] = digest(
        [
            job[k]
            for k in (
                "instance_id",
                "source_id",
                "version",
                "character_id",
                "skin_id",
                "render_key",
            )
        ]
    )
    jobs = [] if empty else [job]
    return {
        "schema_version": 2,
        "instance_id": "i",
        "source_id": "s",
        "version": "5",
        "kind": "automatic",
        "input_snapshot_digest": "b" * 64,
        "tool_fingerprint": "c" * 64,
        "source_capture_ref": ref,
        "core_publication_ref": {
            "scope_key": digest(["i", "s"]),
            "pin_digest": "a" * 64,
            "locator": "core.json",
            "size": 123,
            "sha256": "e" * 64,
        },
        "desired_catalog_ref": {
            "revision": "r",
            "sha256": "e" * 64,
            "locator": "catalog.json",
        },
        "comparison_evidence": {"baseline": empty},
        "recovery": {"max_crashes": 2, "deadline": 1000, "backoff_seconds": 0},
        "jobs": jobs,
        "expected_jobset_digest": digest(jobs),
        "origin_core": {"identity": identity, "outcome": outcome},
        "delivery_disposition": "registered_origin",
    }


def success(c, sha):
    from server_app.portraits.model import PROFILE

    r = {
        k: c[k]
        for k in (
            "schema_version",
            "instance_id",
            "source_id",
            "version",
            "character_id",
            "skin_id",
            "job_id",
            "render_key",
            "profile",
            "attempt",
            "input_snapshot_digest",
            "tool_fingerprint",
            "source_capture_ref",
        )
    }
    r.update(
        status="success",
        diagnostics=[],
        artifact_render_key=c["render_key"],
        artifact_tool_fingerprint=c["tool_fingerprint"],
        source_version=c["version"],
        archive_profile=PROFILE,
        delivery_profile="original",
        width=2,
        height=2,
        archive_width=2,
        archive_height=2,
        artifact_sha256=sha,
        archive_sha256=sha,
        artifact_path=f"artifacts/{sha}.png",
        archive_path=f"archive/{sha}.png",
    )
    return r


def cards(root, scope):
    import hashlib

    folder = root / "outbox" / "5"
    folder.mkdir(parents=True)
    data = b"card immutable fixture"
    (folder / "1.png").write_bytes(data)
    raw = json.dumps(
        {
            "version": 5,
            "characters": [{"character_id": 1, "name": "name", "filename": "1.png"}],
        }
    ).encode()
    sha = hashlib.sha256(raw).hexdigest()
    pin = "b" * 64
    locator = f"metadata/{scope}/{pin}/{sha}.jsonbytes"
    immutable = root / "portrait_sources" / locator
    immutable.parent.mkdir(parents=True)
    immutable.write_bytes(raw)
    (folder / "manifest.json").write_bytes(raw)
    return {
        "kind": "batch",
        "manifest": {
            "scope_key": scope,
            "pin_digest": pin,
            "locator": locator,
            "size": len(raw),
            "sha256": sha,
        },
        "manifest_sha256": sha,
        "keys": ["game-card:5:1:1.png"],
        "files": [
            {"path": "outbox/5/1.png", "sha256": hashlib.sha256(data).hexdigest()}
        ],
    }


def failure_link(evidence, run, promotion):
    value = {
        "run_id": run,
        "scope": evidence.scope,
        "promotion": promotion,
        "state": "failed",
        "code": "source_invalid",
    }
    core._validate(value, evidence.scope, run, "source_links", 3)
    raw = core._encode(value)
    with closing(sqlite3.connect(evidence.core_path)) as db, db:
        previous = db.execute(
            "SELECT sequence,prefix FROM source_links ORDER BY sequence DESC LIMIT 1"
        ).fetchone()
        sequence = previous[0] + 1 if previous else 1
        prefix = core._chain(previous[1] if previous else "0" * 64, sequence, raw)
        db.execute(
            "INSERT INTO source_links VALUES(?,?,?,?)", (sequence, run, raw, prefix)
        )
