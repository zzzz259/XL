"""Domain-specific portrait metadata ledger; authenticated wires are caller preconditions.

This module never authenticates Publication, prepares sources, registers plans or
publishes pointers. Only registration evidence is read from a real PortraitReader.
"""

import os
import sqlite3
import threading
import uuid
from contextlib import contextmanager
from pathlib import Path

from .runtime_ledger_reader import (
    MAX_DOCUMENT,
    MAX_EVENTS,
    MAX_RECORD,
    SCHEMA,
    RuntimeLedgerReader,
    check,
    core,
    encode,
    file_id,
    hash_value,
    number,
    path_check,
    run_key,
    sha,
    text,
)


def _sync(path):
    fd = os.open(path, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    try:
        os.fsync(fd)
    finally:
        os.close(fd)


def _cursor(value, identity, stream):
    fields = {
        "schema",
        "scope",
        "journal_id",
        "file_id",
        "stream",
        "sequence",
        "prefix",
        "high_water",
        "high_prefix",
    }
    if type(value) is not dict or set(value) != fields:
        raise ValueError("full core cursor required")
    if (
        type(value["schema"]) is not int
        or value["schema"] != 1
        or value["stream"] != stream
    ):
        raise ValueError("core cursor schema/stream mismatch")
    if any(value[k] != identity[k] for k in identity):
        raise ValueError("core cursor identity mismatch")
    if (
        any(type(value[k]) is not int for k in ("sequence", "high_water"))
        or not 0 <= value["sequence"] <= value["high_water"]
    ):
        raise ValueError("core cursor position mismatch")
    hash_value(value["prefix"])
    hash_value(value["high_prefix"])
    encode(value)


def _metadata(ref):
    if type(ref) is not dict or set(ref) != {
        "scope_key",
        "pin_digest",
        "locator",
        "size",
        "sha256",
    }:
        raise ValueError("invalid native metadata reference")
    for key in ("scope_key", "pin_digest", "sha256"):
        hash_value(ref[key])
    if type(ref["size"]) is not int or not 0 < ref["size"] <= 32 * MAX_RECORD:
        raise ValueError("invalid metadata size")
    if (
        type(ref["locator"]) is not str
        or not ref["locator"]
        or len(ref["locator"]) > 8192
    ):
        raise ValueError("invalid metadata locator")


class RuntimeLedger(RuntimeLedgerReader):
    """Opt-in persistence. Every mutation requires this object's actual tick lock."""

    def __init__(self, path, instance_id, source_id, *, enabled=False):
        if type(enabled) is not bool:
            raise ValueError("enabled must be boolean")
        self.enabled = enabled
        self._owner = None
        self.path = path_check(path)
        self.instance_id, self.source_id = text(instance_id), text(source_id)
        if not enabled:
            return
        # This leaf is dedicated ledger storage, never a media/cache/work root.
        if os.name != "posix":
            raise RuntimeError(
                "runtime ledger requires Linux flock and directory fsync"
            )
        # A visible ancestor may be an earlier mkdir whose parent fsync failed.
        # Heal every entry barrier, not only directories missing on this retry.
        for directory in reversed(self.path.parents):
            directory.mkdir(mode=0o700, exist_ok=True)
            _sync(directory.parent)
        self.lock_path = Path(str(self.path) + ".tick.lock")
        with self.tick_owned() as owned:
            if not owned:
                raise BlockingIOError("runtime initialization busy")
            db = sqlite3.connect(self.path, timeout=1)
            try:
                initializing = (
                    db.execute("SELECT 1 FROM sqlite_master LIMIT 1").fetchone() is None
                )
                if not initializing:
                    check(db, instance_id, source_id)
                db.execute("PRAGMA journal_mode=WAL")
                db.execute("PRAGMA synchronous=FULL")
                db.execute("BEGIN IMMEDIATE")
                if initializing:
                    for statement in SCHEMA:
                        db.execute(statement)
                    db.execute(
                        "INSERT INTO identity VALUES(1,?,?,?)",
                        (instance_id, source_id, str(uuid.uuid4())),
                    )
                _sync(self.path.parent)
                db.commit()
                _sync(self.path.parent)
            finally:
                db.close()
        super().__init__(path, instance_id, source_id)

    @contextmanager
    def tick_owned(self):
        if not self.enabled:
            yield False
            return
        import fcntl

        if self._owner is not None:
            raise RuntimeError("runtime ownership is not reentrant")
        path_check(self.lock_path)
        fd = os.open(self.lock_path, os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW, 0o600)
        held = False
        try:
            try:
                fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError:
                yield False
                return
            held = True
            _sync(self.path.parent)
            self._owner = (
                os.getpid(),
                threading.get_ident(),
                fd,
                file_id(self.lock_path),
            )
            yield True
        finally:
            if held:
                self._owner = None
                fcntl.flock(fd, fcntl.LOCK_UN)
            os.close(fd)

    def _owned(self):
        import fcntl

        owner = self._owner
        if not owner or owner[:2] != (os.getpid(), threading.get_ident()):
            raise RuntimeError("actual runtime tick ownership required")
        try:
            info = os.fstat(owner[2])
            with open("/proc/self/fdinfo/" + str(owner[2]), encoding="ascii") as handle:
                lock_rows = [
                    line.split()
                    for line in handle.read(4096).splitlines()
                    if line.startswith("lock:")
                ]
        except OSError as exc:
            raise RuntimeError("runtime ownership descriptor unavailable") from exc
        if not any(
            row[2:6] == ["FLOCK", "ADVISORY", "WRITE", str(os.getpid())]
            for row in lock_rows
        ):
            raise RuntimeError("runtime descriptor no longer owns flock")
        if [info.st_dev, info.st_ino] != owner[3] or file_id(self.lock_path) != owner[
            3
        ]:
            raise RuntimeError("runtime ownership file replaced")
        # A separate open description must conflict with the held exclusive lock.
        fd = os.open(self.lock_path, os.O_RDWR | os.O_NOFOLLOW)
        try:
            try:
                fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError:
                return
            fcntl.flock(fd, fcntl.LOCK_UN)
            raise RuntimeError("runtime ownership lock lost")
        finally:
            os.close(fd)

    @contextmanager
    def _write(self):
        self._owned()
        if file_id(self.path) != self.incarnation:
            raise ValueError("runtime file replaced")
        for suffix in ("", "-wal", "-shm"):
            path = path_check(Path(str(self.path) + suffix))
            if path.exists() and path.stat().st_size > 256 * MAX_RECORD:
                raise ValueError("runtime storage bound exceeded")
        db = sqlite3.connect(self.path, timeout=1)
        try:
            check(db, self.instance_id, self.source_id, self.database_id)
            db.execute("PRAGMA synchronous=FULL")
            db.execute("PRAGMA max_page_count=65536")
            db.execute("PRAGMA wal_autocheckpoint=128")
            db.execute("BEGIN IMMEDIATE")
            self._continuity(db)
            yield db
            self._owned()
            if file_id(self.path) != self.incarnation:
                raise ValueError("runtime file replaced")
            _sync(self.path.parent)
            db.commit()
            _sync(self.path.parent)
        finally:
            db.close()

    def _save(self, db, value):
        raw = encode(value)
        key = run_key(value["core_identity"], value["run_id"])
        digest = sha(raw)
        old = db.execute("SELECT digest FROM runs WHERE key=?", (key,)).fetchone()
        if old == (digest,):
            return
        seq, prefix = self._continuity(db)
        if seq >= MAX_EVENTS:
            raise ValueError("runtime transition bound exhausted")
        db.execute("INSERT OR REPLACE INTO runs VALUES(?,?,?)", (key, raw, digest))
        db.execute(
            "INSERT INTO history VALUES(?,?,?,?)",
            (seq + 1, key, digest, sha(encode([prefix, seq + 1, key, digest]))),
        )

    @staticmethod
    def _document(db, value):
        raw = encode(value, MAX_DOCUMENT)
        digest = sha(raw)
        old = db.execute(
            "SELECT payload FROM documents WHERE digest=?", (digest,)
        ).fetchone()
        if old is not None and old != (raw,):
            raise ValueError("immutable document collision")
        db.execute("INSERT OR IGNORE INTO documents VALUES(?,?)", (digest, raw))
        return digest

    @contextmanager
    def _edit(self, identity, run_id):
        with self._write() as db:
            value = self._read(db, run_key(identity, run_id))
            yield db, value
            self._save(db, value)

    def health(self):
        return super().health() if self.enabled else {"enabled": False}

    def observe(
        self,
        core_identity,
        outcome,
        *,
        previous_cursor,
        cursor,
        now,
        stream="outcomes",
        source_link=None,
    ):
        """Persist caller-authenticated original outcome and a COMPLETE cursor atomically."""
        core(core_identity)
        number(now)
        if stream not in ("outcomes", "source_links"):
            raise ValueError("invalid core stream")
        _cursor(cursor, core_identity, stream)
        if previous_cursor is not None:
            _cursor(previous_cursor, core_identity, stream)
            if (
                cursor["sequence"] < previous_cursor["sequence"]
                or cursor["high_water"] < previous_cursor["high_water"]
            ):
                raise ValueError("core cursor rollback")
        encode(outcome)
        item = outcome if stream == "outcomes" else source_link
        if (
            type(item) is not dict
            or type(item.get("sequence")) is not int
            or item["sequence"] != cursor["sequence"]
        ):
            raise ValueError("one exact core stream item required per cursor advance")
        if stream == "source_links":
            encode(source_link)
            if any(
                source_link.get(k) != outcome.get(k)
                for k in ("run_id", "scope", "promotion")
            ) or source_link.get("state") not in ("ready", "failed"):
                raise ValueError("source link original identity mismatch")
        elif source_link is not None:
            raise ValueError("source link requires independent source stream")
        key = run_key(core_identity, outcome["run_id"])
        promotion = outcome.get("promotion")
        if outcome.get("scope") != core_identity["scope"] or (
            promotion is not None and (type(promotion) is not int or promotion < 1)
        ):
            raise ValueError("invalid original outcome identity")
        eligible = promotion is not None and (
            outcome.get("phase") == "published"
            or (
                outcome.get("phase") == "no_change"
                and outcome.get("classification") == "bootstrap"
                and promotion == 1
            )
        )
        text(outcome["version"])
        if outcome.get("pin_digest") is not None:
            hash_value(outcome["pin_digest"])
        with self._write() as db:
            saved_cursors = {
                name: self._read_cursor(db, name)
                for name in ("outcomes", "source_links")
            }
            domain = next(
                (item for item in saved_cursors.values() if item is not None), None
            )
            if domain is not None and any(
                domain.get(k) != v for k, v in core_identity.items()
            ):
                raise ValueError("runtime core domain/incarnation replaced")
            old_cursor = saved_cursors[stream]
            try:
                value = self._read(db, key)
            except KeyError:
                value = None
            if value is not None and value["original_observed"] != outcome:
                raise ValueError("immutable observed outcome conflict")
            if stream == "source_links" and value is None:
                raise ValueError("source link requires durable original outcome")
            if stream == "source_links" and value.get("source_link") not in (
                None,
                source_link,
            ):
                raise ValueError("immutable source link conflict")
            if old_cursor != previous_cursor:
                if value is not None and old_cursor == cursor:
                    return
                raise ValueError("core cursor CAS conflict")
            if value is None:
                budget = {
                    "max_attempts": 3,
                    "attempts": 0,
                    "deadline": now + 300,
                    "delays": [1, 5],
                    "retry_at": now,
                }
                value = {
                    "core_identity": core_identity,
                    "run_id": outcome["run_id"],
                    "original_observed": outcome,
                    "observation_cursor": cursor,
                    "origin_core": {"identity": core_identity, "outcome": outcome},
                    "request_ref": None,
                    "pin": None,
                    "prepared_ref": None,
                    "metadata_phase": "source_pending"
                    if eligible
                    else "ineligible_original_run",
                    "disposition": "pending" if eligible else "ineligible_original_run",
                    "plan_id": None,
                    "delivery_owner": None,
                    "documents": {},
                    "source_budget": dict(budget),
                    "prepare_budget": dict(budget),
                    "registration": None,
                }
                self._save(db, value)
            if stream == "source_links":
                value["source_link"] = source_link
                self._save(db, value)
            raw_cursor = encode(cursor)
            db.execute(
                "INSERT OR REPLACE INTO cursors VALUES(?,?)", (stream, raw_cursor)
            )
            seq, prefix = self._continuity(db)
            if seq >= MAX_EVENTS:
                raise ValueError("runtime transition bound exhausted")
            cursor_key, cursor_digest = "cursor:" + stream, sha(raw_cursor)
            db.execute(
                "INSERT INTO history VALUES(?,?,?,?)",
                (
                    seq + 1,
                    cursor_key,
                    cursor_digest,
                    sha(encode([prefix, seq + 1, cursor_key, cursor_digest])),
                ),
            )

    def _start(self, identity, run_id, now, stage):
        number(now)
        with self._edit(identity, run_id) as (_, value):
            phase = value["metadata_phase"]
            if phase == stage + "_started":
                return False
            allowed = (
                ("source_pending",)
                if stage == "source"
                else ("source_ready", "prepare_pending")
            )
            if phase not in allowed:
                return False
            budget = value[stage + "_budget"]
            if now >= budget["deadline"] or budget["attempts"] >= 3:
                terminal = (
                    "source_resolution_exhausted"
                    if stage == "source"
                    else "preparation_exhausted"
                )
                value.update(metadata_phase=terminal, disposition=terminal)
                return False
            if now < budget["retry_at"]:
                return False
            budget["attempts"] += 1
            value["metadata_phase"] = stage + "_started"
            return True

    def start_source(self, identity, run_id, *, now):
        return self._start(identity, run_id, now, "source")

    def start_prepare(self, identity, run_id, *, now):
        return self._start(identity, run_id, now, "prepare")

    def _retry(self, identity, run_id, now, stage, cooperative):
        number(now)
        if type(cooperative) is not bool:
            raise ValueError("invalid defer classification")
        with self._edit(identity, run_id) as (_, value):
            if value["metadata_phase"] != stage + "_started":
                raise ValueError("no started attempt")
            budget = value[stage + "_budget"]
            if cooperative:
                budget["attempts"] -= 1
            budget["retry_at"] = now + (
                0 if cooperative else [1, 5][min(budget["attempts"] - 1, 1)]
            )
            value["metadata_phase"] = stage + "_pending"

    def retry_source(self, identity, run_id, *, now, cooperative=False):
        self._retry(identity, run_id, now, "source", cooperative)

    def retry_prepare(self, identity, run_id, *, now, cooperative=False):
        self._retry(identity, run_id, now, "prepare", cooperative)

    def source_ready(self, identity, run_id, *, request_ref, pin):
        _metadata(request_ref)
        encode(pin)
        if (
            type(pin) is not dict
            or set(pin) != {"pin_digest", "version"}
            or pin.get("pin_digest") != request_ref["pin_digest"]
        ):
            raise ValueError("native request/pin mismatch")
        with self._edit(identity, run_id) as (_, value):
            if (
                value["metadata_phase"] == "source_ready"
                and value["request_ref"] == request_ref
                and value["pin"] == pin
            ):
                return
            if value["metadata_phase"] != "source_started":
                raise ValueError("source is not started")
            if pin["version"] != value["original_observed"]["version"]:
                raise ValueError("resolved native pin version mismatch")
            if value["original_observed"]["pin_digest"] not in (
                None,
                request_ref["pin_digest"],
            ):
                raise ValueError("original native pin mismatch")
            value.update(
                metadata_phase="source_ready", request_ref=request_ref, pin=pin
            )

    def source_failed(self, identity, run_id, code):
        text(code)
        with self._edit(identity, run_id) as (_, value):
            if (
                value["metadata_phase"] == "source_failed"
                and value.get("failure_code") == code
            ):
                return
            if value["metadata_phase"] not in ("source_pending", "source_started"):
                raise ValueError("source failure conflicts with ready/terminal state")
            value.update(
                metadata_phase="source_failed",
                disposition="source_failed",
                failure_code=code,
            )

    def fail_link_pending(self, identity, run_id):
        with self._edit(identity, run_id) as (_, value):
            if value["metadata_phase"] not in (
                "source_resolution_exhausted",
                "fail_link_pending",
            ):
                raise ValueError("failure link requires exhausted resolution")
            value["metadata_phase"] = "fail_link_pending"

    def prepared(self, identity, run_id, prepared_ref):
        from .store import validate_source_reference

        with self._edit(identity, run_id) as (_, value):
            p = {
                "instance_id": self.instance_id,
                "source_id": self.source_id,
                "version": value["original_observed"]["version"],
                "tool_fingerprint": prepared_ref["tool_fingerprint"],
                "input_snapshot_digest": prepared_ref["snapshot_digest"],
                "source_capture_ref": prepared_ref,
            }
            validate_source_reference(p)
            if prepared_ref["pin_digest"] != value["request_ref"]["pin_digest"]:
                raise ValueError("prepared source pin mismatch")
            if (
                value["metadata_phase"] == "prepared"
                and value["prepared_ref"] == prepared_ref
            ):
                return
            if value["metadata_phase"] != "prepare_started":
                raise ValueError("preparation is not started")
            value.update(metadata_phase="prepared", prepared_ref=prepared_ref)

    def freeze_intent(
        self,
        identity,
        run_id,
        *,
        desired,
        comparator,
        comparison_evidence,
        jobs,
        recovery,
    ):
        from .model import validate_catalog, validate_job

        validate_catalog(desired)
        if comparator is not None:
            validate_catalog(comparator)
        if type(jobs) is not list or len(jobs) > 10000:
            raise ValueError("invalid closed membership size")
        for job in jobs:
            encode(job)
            if type(job.get("schema_version")) is not int or job["schema_version"] != 2:
                raise ValueError("pinned schema2 job required")
            validate_job(job)
        if (
            type(comparison_evidence) is not dict
            or type(comparison_evidence.get("baseline")) is not bool
        ):
            raise ValueError("explicit baseline required")
        if comparison_evidence["baseline"] != (comparator is None) or (
            comparator is None and jobs
        ):
            raise ValueError("baseline must have no prior comparator and zero jobs")
        if (
            type(recovery) is not dict
            or type(recovery.get("max_crashes")) is not int
            or not 1 <= recovery["max_crashes"] <= 100
        ):
            raise ValueError("invalid frozen crash budget")
        number(recovery.get("deadline"))
        if not 0 <= number(recovery.get("backoff_seconds")) <= 86400:
            raise ValueError("invalid frozen recovery backoff")
        documents = {
            "desired": desired,
            "comparator": comparator,
            "comparison_evidence": comparison_evidence,
            "jobs": jobs,
            "recovery": recovery,
        }
        for document in documents.values():
            encode(document, MAX_DOCUMENT)
        with self._edit(identity, run_id) as (db, value):
            if any(
                desired.get(k) != v
                for k, v in {
                    "instance_id": self.instance_id,
                    "source_id": self.source_id,
                    "version": value["original_observed"]["version"],
                }.items()
            ):
                raise ValueError("desired catalog generation mismatch")
            if comparator is not None and (
                comparator["instance_id"],
                comparator["source_id"],
            ) != (self.instance_id, self.source_id):
                raise ValueError("comparator scope mismatch")
            prepared = value["prepared_ref"]
            if prepared is None:
                raise ValueError("intent requires prepared source")
            expected = {
                "instance_id": self.instance_id,
                "source_id": self.source_id,
                "version": prepared["version"],
                "input_snapshot_digest": prepared["snapshot_digest"],
                "tool_fingerprint": prepared["tool_fingerprint"],
                "source_capture_ref": prepared,
            }
            members = set()
            for job in jobs:
                if any(job.get(field) != wanted for field, wanted in expected.items()):
                    raise ValueError("job differs from frozen prepared generation")
                if job["job_id"] in members:
                    raise ValueError("duplicate closed member")
                members.add(job["job_id"])
            refs = {
                name: self._document(db, document)
                for name, document in documents.items()
            }
            if (
                value["metadata_phase"] == "intent_frozen"
                and value["documents"] == refs
            ):
                return
            if value["metadata_phase"] != "prepared":
                raise ValueError("intent requires prepared source")
            value.update(metadata_phase="intent_frozen", documents=refs)
            p = value["prepared_ref"]
            value["frozen_plan_id"] = sha(
                encode(
                    [
                        self.instance_id,
                        self.source_id,
                        "automatic",
                        p["version"],
                        p["snapshot_digest"],
                        p["tool_fingerprint"],
                    ]
                )
            )

    def freeze_manifest(self, identity, run_id, manifest):
        encode(manifest, MAX_DOCUMENT)
        with self._edit(identity, run_id) as (db, value):
            if value["metadata_phase"] in ("registered", "acked") and value[
                "documents"
            ].get("manifest") == sha(encode(manifest, MAX_DOCUMENT)):
                return
            if value["metadata_phase"] not in ("intent_frozen", "manifest_frozen"):
                raise ValueError("manifest requires frozen intent")
            for field, expected in {
                "schema_version": 2,
                "instance_id": self.instance_id,
                "source_id": self.source_id,
                "kind": "automatic",
                "source_capture_ref": value["prepared_ref"],
                "core_publication_ref": value["request_ref"],
                "origin_core": value["origin_core"],
                "delivery_disposition": "registered_origin",
            }.items():
                if manifest.get(field) != expected:
                    raise ValueError("manifest frozen binding mismatch: " + field)
            p = value["prepared_ref"]
            for field, expected in {
                "version": p["version"],
                "input_snapshot_digest": p["snapshot_digest"],
                "tool_fingerprint": p["tool_fingerprint"],
            }.items():
                if manifest.get(field) != expected:
                    raise ValueError("manifest generation mismatch")
            for field in ("jobs", "comparison_evidence", "recovery"):
                if (
                    sha(encode(manifest[field], MAX_DOCUMENT))
                    != value["documents"][field]
                ):
                    raise ValueError("manifest differs from frozen intent")
            if manifest.get("expected_jobset_digest") != sha(
                encode(manifest["jobs"], MAX_DOCUMENT)
            ):
                raise ValueError("manifest jobset mismatch")
            desired_ref = manifest.get("desired_catalog_ref")
            if type(desired_ref) is not dict or set(desired_ref) != {
                "revision",
                "locator",
                "sha256",
            }:
                raise ValueError("invalid desired reference")
            text(desired_ref["revision"])
            hash_value(desired_ref["sha256"])
            if (
                type(desired_ref["locator"]) is not str
                or not desired_ref["locator"]
                or len(desired_ref["locator"]) > 8192
            ):
                raise ValueError("invalid desired locator")
            digest = self._document(db, manifest)
            if (
                "manifest" in value["documents"]
                and value["documents"]["manifest"] != digest
            ):
                raise ValueError("immutable manifest conflict")
            value["documents"]["manifest"] = digest
            value["metadata_phase"] = "manifest_frozen"

    def _registration(self, identity, run_id, reader):
        from .reader import PortraitReader

        if type(reader) is not PortraitReader:
            raise TypeError("actual PortraitReader required")
        if (reader.instance_id, reader.source_id) != (self.instance_id, self.source_id):
            raise ValueError("registration scope mismatch")
        value = self.read_run_mapping(identity, run_id)
        manifest = self.read_intent(identity, run_id)["manifest"]
        pid = value["frozen_plan_id"]
        if encode(reader.read_manifest(pid), MAX_DOCUMENT) != encode(
            manifest, MAX_DOCUMENT
        ):
            raise ValueError("registered manifest conflict")
        snapshot = reader.read_plan_snapshot(pid)
        proof = {
            "database_id": reader.database_id,
            "incarnation": reader.incarnation,
            "path": str(reader.path),
            "plan_id": pid,
            "registration": snapshot["registration"],
        }
        if value["registration"] is not None and value["registration"] != proof:
            raise ValueError("registered store replaced")
        return proof

    def prove_registered(self, identity, run_id, reader):
        self._owned()
        proof = self._registration(identity, run_id, reader)
        with self._edit(identity, run_id) as (_, value):
            if value["metadata_phase"] not in (
                "manifest_frozen",
                "registered",
                "acked",
            ):
                raise ValueError("registration requires final manifest")
            value.update(
                registration=proof,
                plan_id=proof["plan_id"],
                disposition="registered_origin",
                delivery_owner={"core_identity": identity, "run_id": run_id},
            )
            if value["metadata_phase"] != "acked":
                value["metadata_phase"] = "registered"

    def ack(self, identity, run_id):
        with self._edit(identity, run_id) as (_, value):
            if (
                value["metadata_phase"] not in ("registered", "acked")
                or value["registration"] is None
            ):
                raise ValueError("ACK requires proven registration")
            value["metadata_phase"] = "acked"

    def alias(
        self,
        identity,
        run_id,
        origin_identity,
        origin_run_id,
        *,
        prepared_ref,
        desired,
        reader,
    ):
        self._owned()
        origin = self.read_run_mapping(origin_identity, origin_run_id)
        if origin["metadata_phase"] not in ("registered", "acked"):
            raise ValueError("alias requires registered original")
        self._registration(origin_identity, origin_run_id, reader)
        frozen = self.read_intent(origin_identity, origin_run_id)
        if prepared_ref != origin["prepared_ref"] or encode(
            desired, MAX_DOCUMENT
        ) != encode(frozen["desired"], MAX_DOCUMENT):
            raise ValueError("automatic identity collision")
        with self._edit(identity, run_id) as (_, value):
            if value["metadata_phase"] not in (
                "source_ready",
                "prepared",
                "existing_plan_alias",
            ):
                raise ValueError("alias conflicts with frozen intent")
            if (
                identity != origin_identity
                or value["original_observed"]["promotion"]
                <= origin["original_observed"]["promotion"]
                or value["original_observed"]["version"] != prepared_ref["version"]
                or value["original_observed"]["pin_digest"]
                not in (None, prepared_ref["pin_digest"])
                or value["pin"]
                != {
                    "version": prepared_ref["version"],
                    "pin_digest": prepared_ref["pin_digest"],
                }
                or value["request_ref"]["pin_digest"] != prepared_ref["pin_digest"]
            ):
                raise ValueError("alias original authority mismatch")
            if (
                value["prepared_ref"] is not None
                and value["prepared_ref"] != prepared_ref
            ):
                raise ValueError("alias conflicts with already prepared evidence")
            value.update(
                metadata_phase="existing_plan_alias",
                disposition="existing_plan_alias",
                plan_id=origin["plan_id"],
                origin_core=origin["origin_core"],
                delivery_owner=origin["delivery_owner"],
                registration=origin["registration"],
                prepared_ref=prepared_ref,
            )

    def supersede_unregistered(self, identity, run_id, reader):
        """Caller proves obsolescence; existing registration always wins over supersession."""
        from .reader import PortraitReader

        self._owned()
        if type(reader) is not PortraitReader or (
            reader.instance_id,
            reader.source_id,
        ) != (self.instance_id, self.source_id):
            raise ValueError("actual scoped PortraitReader required")
        value = self.read_run_mapping(identity, run_id)
        if value["plan_id"] is not None:
            self._registration(identity, run_id, reader)
            return False
        if "frozen_plan_id" in value:
            try:
                reader.read_manifest(value["frozen_plan_id"])
            except KeyError:
                pass
            else:
                self.prove_registered(identity, run_id, reader)
                return False
        with self._edit(identity, run_id) as (_, value):
            if value["metadata_phase"] in (
                "source_failed",
                "source_resolution_exhausted",
                "fail_link_pending",
                "preparation_exhausted",
            ):
                raise ValueError("terminal resolution is not supersession")
            value.update(
                metadata_phase="superseded_before_registration",
                disposition="superseded_before_registration",
            )
        return True
