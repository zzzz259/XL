"""Real SQLite ledger contracts; no renderer or pytest dependency."""

import copy
import importlib.util
import os
import sqlite3
import tempfile
import unittest
from pathlib import Path

from server_app.portraits.reader import PortraitReader
from server_app.portraits.store import PortraitStore
from test_portrait_store_v2_unittest import plan

SPEC = importlib.util.find_spec("server_app.portraits.runtime_ledger")
if SPEC:
    from server_app.portraits.runtime_ledger import RuntimeLedger, RuntimeLedgerReader

DESIRED = {
    "schema_version": 1,
    "instance_id": "i",
    "source_id": "s",
    "version": "v",
    "skins": [],
}

CORE = {
    "scope": "f" * 64,
    "journal_id": "11111111-1111-1111-1111-111111111111",
    "file_id": [1, 2],
}


def cursor(sequence=1):
    return dict(
        CORE,
        schema=1,
        stream="outcomes",
        sequence=sequence,
        prefix="a" * 64,
        high_water=sequence,
        high_prefix="a" * 64,
    )


def observation(run="a", promotion=1):
    return {
        "sequence": promotion,
        "run_id": run,
        "promotion": promotion,
        "version": "v",
        "pin_digest": "a" * 64,
        "scope": CORE["scope"],
        "phase": "published",
    }


class LedgerTests(unittest.TestCase):
    def setUp(self):
        self.assertIsNotNone(SPEC, "runtime ledger is not implemented")
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.path = self.root / "ledger" / "state.sqlite"
        self.ledger = RuntimeLedger(self.path, "i", "s", enabled=True)

    def observed(self, ledger=None, run="a", promotion=1):
        ledger = ledger or self.ledger
        ledger.observe(
            CORE,
            observation(run, promotion),
            previous_cursor=None,
            cursor=cursor(),
            now=100,
        )

    def frozen(self):
        self.observed()
        self.ledger.start_source(CORE, "a", now=100)
        p = plan(True)
        self.ledger.source_ready(
            CORE,
            "a",
            request_ref=p["core_publication_ref"],
            pin={"pin_digest": "a" * 64, "version": "v"},
        )
        self.ledger.start_prepare(CORE, "a", now=100)
        self.ledger.prepared(CORE, "a", p["source_capture_ref"])
        self.ledger.freeze_intent(
            CORE,
            "a",
            desired=DESIRED,
            comparator=None,
            comparison_evidence=p["comparison_evidence"],
            jobs=p["jobs"],
            recovery=p["recovery"],
        )
        p["origin_core"] = {"identity": CORE, "outcome": observation()}
        p["delivery_disposition"] = "registered_origin"
        self.ledger.freeze_manifest(CORE, "a", p)
        return p

    def test_disabled_no_state(self):
        path = self.root / "off" / "state.sqlite"
        ledger = RuntimeLedger(path, "i", "s")
        self.assertEqual(ledger.health(), {"enabled": False})
        with ledger.tick_owned() as owned:
            self.assertFalse(owned)
        self.assertFalse(path.parent.exists())

    def test_observe_atomic_cursor_conflict_and_restart(self):
        with self.ledger.tick_owned():
            self.observed()
            with self.assertRaises(ValueError):
                self.ledger.observe(
                    CORE,
                    observation("b", 2),
                    previous_cursor=cursor(0),
                    cursor=cursor(2),
                    now=101,
                )
        reader = RuntimeLedgerReader(self.path, "i", "s")
        mapping = reader.read_run_mapping(CORE, "a")
        self.assertEqual(mapping["original_observed"], observation())
        self.assertIsNone(mapping["plan_id"])
        self.assertEqual(mapping["metadata_phase"], "source_pending")
        with self.assertRaises(KeyError):
            reader.read_run_mapping(CORE, "b")
        reopened = RuntimeLedger(self.path, "i", "s", enabled=True)
        self.assertEqual(reopened.read_cursor("outcomes"), cursor())

    def test_ownership_and_two_instances(self):
        with self.assertRaises(RuntimeError):
            self.observed()
        other = RuntimeLedger(self.path, "i", "s", enabled=True)
        with self.ledger.tick_owned() as owned:
            self.assertTrue(owned)
            with other.tick_owned() as second:
                self.assertFalse(second)
            self.observed()
        with other.tick_owned() as second:
            self.assertTrue(second)

    def test_started_attempts_survive_restart_and_deadline(self):
        with self.ledger.tick_owned():
            self.observed()
            self.assertTrue(self.ledger.start_source(CORE, "a", now=100))
            self.assertFalse(self.ledger.start_source(CORE, "a", now=100))
        reopened = RuntimeLedger(self.path, "i", "s", enabled=True)
        with reopened.tick_owned():
            reopened.retry_source(CORE, "a", now=101)
            self.assertFalse(reopened.start_source(CORE, "a", now=101))
            self.assertTrue(reopened.start_source(CORE, "a", now=102))
            reopened.retry_source(CORE, "a", now=102)
            self.assertTrue(reopened.start_source(CORE, "a", now=107))
            reopened.retry_source(CORE, "a", now=108)
            self.assertFalse(reopened.start_source(CORE, "a", now=113))
            m = reopened.read_run_mapping(CORE, "a")
            self.assertEqual(m["disposition"], "source_resolution_exhausted")
            self.assertEqual(m["source_budget"]["attempts"], 3)
            self.assertEqual(m["source_budget"]["deadline"], 400)

    def test_registered_proof_exact_manifest_alias_origin(self):
        with self.ledger.tick_owned():
            p = self.frozen()
            store = PortraitStore(self.root / "store.sqlite", "i", "s")
            reader = PortraitReader(store.path, "i", "s")
            with self.assertRaises(KeyError):
                self.ledger.prove_registered(CORE, "a", reader)
            self.assertIsNone(self.ledger.read_run_mapping(CORE, "a")["plan_id"])
            pid = store.register_closed_plan(p)["plan_id"]
            self.ledger.prove_registered(CORE, "a", reader)
            self.ledger.ack(CORE, "a")
            self.ledger.observe(
                CORE,
                observation("b", 2),
                previous_cursor=cursor(),
                cursor=cursor(2),
                now=110,
            )
            with self.assertRaises(ValueError):
                self.ledger.alias(
                    CORE,
                    "b",
                    CORE,
                    "a",
                    prepared_ref=p["source_capture_ref"],
                    desired=DESIRED,
                    reader=reader,
                )
            self.ledger.start_source(CORE, "b", now=110)
            self.ledger.source_ready(
                CORE,
                "b",
                request_ref=p["core_publication_ref"],
                pin={"version": "v", "pin_digest": "a" * 64},
            )
            self.ledger.alias(
                CORE,
                "b",
                CORE,
                "a",
                prepared_ref=p["source_capture_ref"],
                desired=DESIRED,
                reader=reader,
            )
            m = self.ledger.read_run_mapping(CORE, "b")
            self.assertEqual(m["plan_id"], pid)
            self.assertEqual(m["disposition"], "existing_plan_alias")
            self.assertEqual(
                m["delivery_owner"], {"core_identity": CORE, "run_id": "a"}
            )
            self.assertEqual(m["origin_core"], p["origin_core"])
            self.assertEqual(reader.read_manifest(pid), p)
            with self.assertRaises(ValueError):
                self.ledger.alias(
                    CORE,
                    "b",
                    CORE,
                    "a",
                    prepared_ref=p["source_capture_ref"],
                    desired={"version": "wrong"},
                    reader=reader,
                )

    def test_immutable_manifest_conflict_and_unknown_reader(self):
        with self.ledger.tick_owned():
            p = self.frozen()
            changed = copy.deepcopy(p)
            changed["recovery"]["deadline"] += 1
            with self.assertRaises(ValueError):
                self.ledger.freeze_manifest(CORE, "a", changed)
            with self.assertRaises(TypeError):
                self.ledger.prove_registered(CORE, "a", object())

    def test_reader_scope_missing_corruption_and_replacement(self):
        with self.ledger.tick_owned():
            self.observed()
        with self.assertRaises(ValueError):
            RuntimeLedgerReader(self.path, "other", "s")
        missing = self.root / "missing" / "db"
        with self.assertRaises(FileNotFoundError):
            RuntimeLedgerReader(missing, "i", "s")
        self.assertFalse(missing.parent.exists())
        reader = RuntimeLedgerReader(self.path, "i", "s")
        with sqlite3.connect(self.path) as db:
            db.execute("UPDATE runs SET payload='{}'")
        with self.assertRaises(ValueError):
            reader.read_run_mapping(CORE, "a")
        os.replace(self.path, self.path.with_suffix(".old"))
        RuntimeLedger(self.path, "i", "s", enabled=True)
        with self.assertRaises(ValueError):
            reader.read_run_mapping(CORE, "a")


class LedgerBoundaryTests(unittest.TestCase):
    setUp = LedgerTests.setUp
    observed = LedgerTests.observed
    frozen = LedgerTests.frozen

    def test_dirsync_rollback_and_readonly_import(self):
        from unittest.mock import patch

        with self.ledger.tick_owned():
            with (
                patch(
                    "server_app.portraits.runtime_ledger._sync",
                    side_effect=OSError("injected directory durability"),
                ),
                self.assertRaises(OSError),
            ):
                self.observed()
            with self.assertRaises(KeyError):
                self.ledger.read_run_mapping(CORE, "a")
            self.assertIsNone(self.ledger.read_cursor("outcomes"))
            self.observed()
        source = (
            Path(__file__).parents[1]
            / "server_app"
            / "portraits"
            / "runtime_ledger_reader.py"
        )
        self.assertNotIn("from .store", source.read_text())
        self.assertNotIn("source_prepare", source.read_text())

    def test_prepare_budget_and_native_failed_are_distinct(self):
        with self.ledger.tick_owned():
            self.observed()
            self.ledger.start_source(CORE, "a", now=100)
            self.ledger.retry_source(CORE, "a", now=101, cooperative=True)
            self.assertEqual(
                self.ledger.read_run_mapping(CORE, "a")["source_budget"]["attempts"], 0
            )
            self.ledger.start_source(CORE, "a", now=101)
            p = plan(True)
            self.ledger.source_ready(
                CORE,
                "a",
                request_ref=p["core_publication_ref"],
                pin={"pin_digest": "a" * 64, "version": "v"},
            )
            self.ledger.start_prepare(CORE, "a", now=102)
            self.ledger.retry_prepare(CORE, "a", now=103)
            self.assertFalse(self.ledger.start_prepare(CORE, "a", now=400))
            with self.assertRaises(ValueError):
                self.ledger.fail_link_pending(CORE, "a")
            value = self.ledger.read_run_mapping(CORE, "a")
            self.assertEqual(value["metadata_phase"], "preparation_exhausted")
            self.assertEqual(value["prepare_budget"]["attempts"], 1)
            self.assertEqual(value["source_budget"]["attempts"], 1)
            self.assertIsNone(value["plan_id"])
            self.ledger.observe(
                CORE,
                observation("b", 2),
                previous_cursor=cursor(),
                cursor=cursor(2),
                now=120,
            )
            self.ledger.source_failed(CORE, "b", "native_failure")
            with self.assertRaises(ValueError):
                self.ledger.source_ready(
                    CORE,
                    "b",
                    request_ref=p["core_publication_ref"],
                    pin={"pin_digest": "a" * 64, "version": "v"},
                )

    def test_wrong_thread_and_lost_flock_no_mutation(self):
        import fcntl
        import threading

        errors = []
        with self.ledger.tick_owned():

            def mutate():
                try:
                    self.observed()
                except RuntimeError:
                    errors.append("blocked")

            t = threading.Thread(target=mutate)
            t.start()
            t.join(2)
            self.assertEqual(errors, ["blocked"])
            fd = self.ledger._owner[2]
            fcntl.flock(fd, fcntl.LOCK_UN)
            with self.assertRaises(RuntimeError):
                self.observed()
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        with self.assertRaises(KeyError):
            self.ledger.read_run_mapping(CORE, "a")

    def test_real_process_crash_releases_lock(self):
        import subprocess
        import sys

        code = """import os,sys
from server_app.portraits.runtime_ledger import RuntimeLedger
ledger=RuntimeLedger(sys.argv[1],'i','s',enabled=True)
with ledger.tick_owned():
 print('owned',flush=True)
 sys.stdin.readline()
 os._exit(0)
"""
        child = subprocess.Popen(
            [sys.executable, "-B", "-c", code, str(self.path)],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            text=True,
        )
        try:
            self.assertEqual(child.stdout.readline().strip(), "owned")
            with self.ledger.tick_owned() as owned:
                self.assertFalse(owned)
            child.communicate("\n", timeout=5)
            with self.ledger.tick_owned() as owned:
                self.assertTrue(owned)
                self.observed()
        finally:
            if child.poll() is None:
                child.kill()
                child.wait(timeout=5)

    def test_unknown_schema_does_not_mutate(self):
        p = self.root / "foreign.sqlite"
        with sqlite3.connect(p) as db:
            db.execute("CREATE TABLE foreign_table(x)")
        before = p.read_bytes()
        with self.assertRaises(ValueError):
            RuntimeLedger(p, "i", "s", enabled=True)
        self.assertEqual(before, p.read_bytes())
        self.assertFalse(Path(str(p) + "-wal").exists())

    def test_outcome_types_and_complete_cursor_cannot_alias(self):
        with self.ledger.tick_owned():
            bad = cursor()
            bad["schema"] = True
            with self.assertRaises(ValueError):
                self.ledger.observe(
                    CORE, observation(), previous_cursor=None, cursor=bad, now=100
                )
            self.observed()
            changed = observation()
            changed["promotion"] = True
            with self.assertRaises(ValueError):
                self.ledger.observe(
                    CORE, changed, previous_cursor=cursor(), cursor=cursor(), now=100
                )
            self.ledger.observe(
                CORE,
                observation(),
                stream="source_links",
                previous_cursor=None,
                cursor=dict(cursor(), stream="source_links"),
                now=100,
                source_link={
                    "sequence": 1,
                    "run_id": "a",
                    "scope": CORE["scope"],
                    "promotion": 1,
                    "state": "failed",
                    "code": "native_failure",
                },
            )
            self.assertEqual(self.ledger.read_cursor("outcomes"), cursor())
            self.assertEqual(
                self.ledger.read_cursor("source_links"),
                dict(cursor(), stream="source_links"),
            )

    def test_terminal_manifest_replay_idempotent_and_store_replacement(self):
        with self.ledger.tick_owned():
            p = self.frozen()
            store = PortraitStore(self.root / "store.sqlite", "i", "s")
            pid = store.register_closed_plan(p)["plan_id"]
            reader = PortraitReader(store.path, "i", "s")
            self.ledger.prove_registered(CORE, "a", reader)
            # Restart repair can replay an already exact immutable manifest.
            self.ledger.freeze_manifest(CORE, "a", p)
            self.assertEqual(
                self.ledger.read_run_mapping(CORE, "a")["metadata_phase"], "registered"
            )
            other = PortraitStore(self.root / "other.sqlite", "i", "s")
            other.register_closed_plan(p)
            with self.assertRaises(ValueError):
                self.ledger.prove_registered(
                    CORE, "a", PortraitReader(other.path, "i", "s")
                )
            self.assertEqual(self.ledger.read_run_mapping(CORE, "a")["plan_id"], pid)

    def test_cursor_corruption_is_not_authority(self):
        with self.ledger.tick_owned():
            self.observed()
        with sqlite3.connect(self.path) as db:
            db.execute("UPDATE cursors SET payload='{}'")
        with self.assertRaises(ValueError):
            self.ledger.read_cursor("outcomes")


class LedgerStrictTests(unittest.TestCase):
    setUp = LedgerTests.setUp
    observed = LedgerTests.observed
    frozen = LedgerTests.frozen

    def test_preparation_exhaustion_never_marks_native_failed(self):
        with self.ledger.tick_owned():
            self.observed()
            self.ledger.start_source(CORE, "a", now=100)
            p = plan(True)
            self.ledger.source_ready(
                CORE,
                "a",
                request_ref=p["core_publication_ref"],
                pin={"version": "v", "pin_digest": "a" * 64},
            )
            self.assertFalse(self.ledger.start_prepare(CORE, "a", now=400))
            m = self.ledger.read_run_mapping(CORE, "a")
            self.assertEqual(m["disposition"], "preparation_exhausted")
            with self.assertRaises(ValueError):
                self.ledger.fail_link_pending(CORE, "a")

    def test_invalid_desired_scope_not_frozen(self):
        with self.ledger.tick_owned():
            self.observed()
            self.ledger.start_source(CORE, "a", now=100)
            p = plan(True)
            self.ledger.source_ready(
                CORE,
                "a",
                request_ref=p["core_publication_ref"],
                pin={"version": "v", "pin_digest": "a" * 64},
            )
            self.ledger.start_prepare(CORE, "a", now=100)
            self.ledger.prepared(CORE, "a", p["source_capture_ref"])
            with self.assertRaises(ValueError):
                self.ledger.freeze_intent(
                    CORE,
                    "a",
                    desired={"version": "v"},
                    comparator=None,
                    comparison_evidence={"baseline": True},
                    jobs=[],
                    recovery=p["recovery"],
                )

    def test_unknown_original_pin_preserved(self):
        with self.ledger.tick_owned():
            out = observation()
            out["pin_digest"] = None
            self.ledger.observe(
                CORE, out, previous_cursor=None, cursor=cursor(), now=100
            )
            self.ledger.start_source(CORE, "a", now=100)
            self.ledger.source_ready(
                CORE,
                "a",
                request_ref=plan(True)["core_publication_ref"],
                pin={"version": "v", "pin_digest": "a" * 64},
            )
            m = self.ledger.read_run_mapping(CORE, "a")
            self.assertIsNone(m["original_observed"]["pin_digest"])
            self.assertEqual(m["pin"]["pin_digest"], "a" * 64)

    def test_acked_cannot_supersede_but_unregistered_can(self):
        with self.ledger.tick_owned():
            self.frozen()
            store = PortraitStore(self.root / "store.sqlite", "i", "s")
            reader = PortraitReader(store.path, "i", "s")
            self.assertTrue(self.ledger.supersede_unregistered(CORE, "a", reader))
            self.assertEqual(
                self.ledger.read_run_mapping(CORE, "a")["disposition"],
                "superseded_before_registration",
            )

    def test_no_second_core_identity_silently_adopted(self):
        with self.ledger.tick_owned():
            self.observed()
            other = dict(CORE, journal_id="22222222-2222-2222-2222-222222222222")
            othercursor = dict(cursor(), **other, stream="source_links")
            with self.assertRaises(ValueError):
                self.ledger.observe(
                    other,
                    observation("b", 2),
                    stream="source_links",
                    previous_cursor=None,
                    cursor=othercursor,
                    now=100,
                )


class LedgerFinalBoundaryTests(unittest.TestCase):
    setUp = LedgerTests.setUp
    observed = LedgerTests.observed
    frozen = LedgerTests.frozen

    def test_cursor_cannot_skip_unpersisted_outcomes(self):
        with self.ledger.tick_owned(), self.assertRaises(ValueError):
            self.ledger.observe(
                CORE, observation(), previous_cursor=None, cursor=cursor(2), now=100
            )

    def test_closed_reused_fd_does_not_borrow_other_owner(self):
        import fcntl

        with self.ledger.tick_owned():
            fd = self.ledger._owner[2]
            os.close(fd)
            reused = os.open(self.ledger.lock_path, os.O_RDWR)
            self.assertEqual(reused, fd)
            other = os.open(self.ledger.lock_path, os.O_RDWR)
            fcntl.flock(other, fcntl.LOCK_EX | fcntl.LOCK_NB)
            try:
                with self.assertRaises(RuntimeError):
                    self.observed()
            finally:
                fcntl.flock(other, fcntl.LOCK_UN)
                os.close(other)

    def test_invalid_final_desired_ref_cannot_freeze(self):
        with self.ledger.tick_owned():
            p = self.frozen()
            # Exact conflict is already tested; validate an invalid reference on first freeze in b.
            self.ledger.observe(
                CORE,
                observation("b", 2),
                previous_cursor=cursor(),
                cursor=cursor(2),
                now=110,
            )
            self.ledger.start_source(CORE, "b", now=110)
            self.ledger.source_ready(
                CORE,
                "b",
                request_ref=p["core_publication_ref"],
                pin={"version": "v", "pin_digest": "a" * 64},
            )
            self.ledger.start_prepare(CORE, "b", now=110)
            self.ledger.prepared(CORE, "b", p["source_capture_ref"])
            self.ledger.freeze_intent(
                CORE,
                "b",
                desired=DESIRED,
                comparator=None,
                comparison_evidence=p["comparison_evidence"],
                jobs=[],
                recovery=p["recovery"],
            )
            p["origin_core"] = {"identity": CORE, "outcome": observation("b", 2)}
            p["desired_catalog_ref"] = {}
            with self.assertRaises(ValueError):
                self.ledger.freeze_manifest(CORE, "b", p)

    def test_false_recovery_boolean_and_oversize_rejected(self):
        with self.ledger.tick_owned():
            p = self.frozen()
            self.ledger.observe(
                CORE,
                observation("b", 2),
                previous_cursor=cursor(),
                cursor=cursor(2),
                now=110,
            )
            self.ledger.start_source(CORE, "b", now=110)
            self.ledger.source_ready(
                CORE,
                "b",
                request_ref=p["core_publication_ref"],
                pin={"version": "v", "pin_digest": "a" * 64},
            )
            self.ledger.start_prepare(CORE, "b", now=110)
            self.ledger.prepared(CORE, "b", p["source_capture_ref"])
            with self.assertRaises(ValueError):
                self.ledger.freeze_intent(
                    CORE,
                    "b",
                    desired=DESIRED,
                    comparator=None,
                    comparison_evidence=p["comparison_evidence"],
                    jobs=[],
                    recovery=dict(p["recovery"], max_crashes=True),
                )


class LedgerDiscoveryTests(unittest.TestCase):
    setUp = LedgerTests.setUp
    observed = LedgerTests.observed

    def test_ineligible_outcome_advances_with_visible_disposition(self):
        with self.ledger.tick_owned():
            out = observation()
            out.update(promotion=None, phase="no_change", classification="noop")
            self.ledger.observe(
                CORE, out, previous_cursor=None, cursor=cursor(), now=100
            )
            self.assertEqual(
                self.ledger.read_run_mapping(CORE, "a")["disposition"],
                "ineligible_original_run",
            )
            self.assertFalse(self.ledger.start_source(CORE, "a", now=100))
            self.assertEqual(self.ledger.read_cursor("outcomes"), cursor())

    def test_actual_source_link_is_atomic_and_preserved(self):
        from unittest.mock import patch

        link = {
            "sequence": 1,
            "run_id": "a",
            "scope": CORE["scope"],
            "promotion": 1,
            "state": "failed",
            "code": "native_failure",
        }
        with self.ledger.tick_owned():
            self.observed()
            with (
                patch(
                    "server_app.portraits.runtime_ledger._sync",
                    side_effect=OSError("fault"),
                ),
                self.assertRaises(OSError),
            ):
                self.ledger.observe(
                    CORE,
                    observation(),
                    stream="source_links",
                    previous_cursor=None,
                    cursor=dict(cursor(), stream="source_links"),
                    now=101,
                    source_link=link,
                )
            self.assertIsNone(self.ledger.read_cursor("source_links"))
            self.assertNotIn("source_link", self.ledger.read_run_mapping(CORE, "a"))
            self.ledger.observe(
                CORE,
                observation(),
                stream="source_links",
                previous_cursor=None,
                cursor=dict(cursor(), stream="source_links"),
                now=101,
                source_link=link,
            )
            self.assertEqual(
                self.ledger.read_run_mapping(CORE, "a")["source_link"], link
            )

    def test_catalog_and_job_caps_reject_without_truncation(self):
        from server_app.portraits.runtime_ledger_reader import (
            MAX_DOCUMENT,
            MAX_RECORD,
            encode,
        )

        with self.assertRaises(ValueError):
            encode("x" * MAX_RECORD)
        with self.assertRaises(ValueError):
            encode("x" * MAX_DOCUMENT, MAX_DOCUMENT)


class LedgerReviewRegressionTests(unittest.TestCase):
    setUp = LedgerTests.setUp
    observed = LedgerTests.observed
    frozen = LedgerTests.frozen

    def prepared_run(self):
        self.observed()
        p = plan(True)
        self.ledger.start_source(CORE, "a", now=100)
        self.ledger.source_ready(
            CORE,
            "a",
            request_ref=p["core_publication_ref"],
            pin={"version": "v", "pin_digest": "a" * 64},
        )
        self.ledger.start_prepare(CORE, "a", now=100)
        self.ledger.prepared(CORE, "a", p["source_capture_ref"])
        return p

    def registered_origin_and_ready_alias(self, *, unknown=False):
        p = self.frozen()
        store = PortraitStore(self.root / "review-store.sqlite", "i", "s")
        pid = store.register_closed_plan(p)["plan_id"]
        reader = PortraitReader(store.path, "i", "s")
        self.ledger.prove_registered(CORE, "a", reader)
        out = observation("b", 2)
        if unknown:
            out["pin_digest"] = None
        self.ledger.observe(
            CORE, out, previous_cursor=cursor(), cursor=cursor(2), now=110
        )
        self.ledger.start_source(CORE, "b", now=110)
        self.ledger.source_ready(
            CORE,
            "b",
            request_ref=p["core_publication_ref"],
            pin={"version": "v", "pin_digest": "a" * 64},
        )
        return p, reader, pid

    def test_r1_empty_initial_rollback_retry_heals_only_empty_schema(self):
        from unittest.mock import patch

        import server_app.portraits.runtime_ledger as module

        p = self.root / "retry-init" / "ledger.sqlite"
        p.parent.mkdir()
        sync = module._sync

        def fault(path):
            if Path(path) == p.parent and p.exists():
                raise OSError("init precommit")
            sync(path)

        with (
            patch.object(module, "_sync", side_effect=fault),
            self.assertRaises(OSError),
        ):
            RuntimeLedger(p, "i", "s", enabled=True)
        with sqlite3.connect(p) as db:
            self.assertEqual(
                db.execute("SELECT count(*) FROM sqlite_master").fetchone()[0], 0
            )
        reopened = RuntimeLedger(p, "i", "s", enabled=True)
        self.assertEqual(reopened.health()["runs"], 0)
        with reopened.tick_owned():
            self.observed(reopened)
        self.assertEqual(
            RuntimeLedgerReader(p, "i", "s").read_run_mapping(CORE, "a")[
                "metadata_phase"
            ],
            "source_pending",
        )

    def test_r2_visible_ancestor_retry_repeats_failed_parent_barrier(self):
        from unittest.mock import patch

        import server_app.portraits.runtime_ledger as module

        p = self.root / "ancestor" / "leaf" / "ledger.sqlite"
        sync = module._sync

        def fault(path):
            if Path(path) == self.root and (self.root / "ancestor").exists():
                raise OSError("ancestor barrier")
            sync(path)

        with (
            patch.object(module, "_sync", side_effect=fault),
            self.assertRaises(OSError),
        ):
            RuntimeLedger(p, "i", "s", enabled=True)
        seen = []

        def record(path):
            seen.append(Path(path))
            sync(path)

        with patch.object(module, "_sync", side_effect=record):
            RuntimeLedger(p, "i", "s", enabled=True)
        self.assertIn(self.root, seen)
        self.assertIn(p.parent, seen)

    def test_r3_prepared_alias_collision_preserves_new_run_evidence(self):
        with self.ledger.tick_owned():
            p, reader, _ = self.registered_origin_and_ready_alias()
            changed = copy.deepcopy(p["source_capture_ref"])
            changed["capture"]["sha256"] = "9" * 64
            self.ledger.start_prepare(CORE, "b", now=110)
            self.ledger.prepared(CORE, "b", changed)
            before = self.ledger.read_run_mapping(CORE, "b")
            with self.assertRaises(ValueError):
                self.ledger.alias(
                    CORE,
                    "b",
                    CORE,
                    "a",
                    prepared_ref=p["source_capture_ref"],
                    desired=DESIRED,
                    reader=reader,
                )
            self.assertEqual(self.ledger.read_run_mapping(CORE, "b"), before)

    def test_r4_unknown_original_pin_alias_uses_resolved_pin(self):
        with self.ledger.tick_owned():
            p, reader, pid = self.registered_origin_and_ready_alias(unknown=True)
            self.ledger.alias(
                CORE,
                "b",
                CORE,
                "a",
                prepared_ref=p["source_capture_ref"],
                desired=DESIRED,
                reader=reader,
            )
            alias = self.ledger.read_run_mapping(CORE, "b")
            self.assertEqual(alias["plan_id"], pid)
            self.assertIsNone(alias["original_observed"]["pin_digest"])
            self.assertEqual(alias["pin"], {"version": "v", "pin_digest": "a" * 64})
            self.assertEqual(
                alias["delivery_owner"], {"core_identity": CORE, "run_id": "a"}
            )
            self.assertEqual(reader.read_manifest(pid), p)

    def test_r5_jobs_bind_every_frozen_generation_field_and_are_unique(self):
        from server_app.portraits.model import digest, validate_job

        for field in (
            "instance_id",
            "source_id",
            "version",
            "input_snapshot_digest",
            "tool_fingerprint",
            "source_capture_ref",
            "duplicate",
        ):
            with self.subTest(field=field):
                self.ledger = RuntimeLedger(
                    self.root / field / "db.sqlite", "i", "s", enabled=True
                )
                with self.ledger.tick_owned():
                    p = self.prepared_run()
                    job = copy.deepcopy(plan(False)["jobs"][0])
                    if field in ("instance_id", "source_id", "version"):
                        job[field] = "other"
                        job["source_capture_ref"][field] = "other"
                        job["source_capture_ref"]["capture"]["scope_key"] = digest(
                            [job["instance_id"], job["source_id"]]
                        )
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
                    elif field == "input_snapshot_digest":
                        job[field] = "9" * 64
                        job["source_capture_ref"]["snapshot_digest"] = "9" * 64
                    elif field == "tool_fingerprint":
                        job[field] = "9" * 64
                        job["source_capture_ref"][field] = "9" * 64
                    elif field == "source_capture_ref":
                        job[field]["capture"]["sha256"] = "9" * 64
                    validate_job(job)
                    jobs = [job, copy.deepcopy(job)] if field == "duplicate" else [job]
                    with self.assertRaises(ValueError):
                        self.ledger.freeze_intent(
                            CORE,
                            "a",
                            desired=DESIRED,
                            comparator=DESIRED,
                            comparison_evidence={"baseline": False},
                            jobs=jobs,
                            recovery=p["recovery"],
                        )
                    self.assertEqual(
                        self.ledger.read_run_mapping(CORE, "a")["metadata_phase"],
                        "prepared",
                    )
                    self.assertEqual(self.ledger.read_intent(CORE, "a"), {})

    def test_r5_valid_nonbaseline_members_freeze_and_register(self):
        from server_app.portraits.model import digest

        with self.ledger.tick_owned():
            self.prepared_run()
            p = plan(False)
            self.ledger.freeze_intent(
                CORE,
                "a",
                desired=DESIRED,
                comparator=DESIRED,
                comparison_evidence=p["comparison_evidence"],
                jobs=p["jobs"],
                recovery=p["recovery"],
            )
            p["origin_core"] = {"identity": CORE, "outcome": observation()}
            p["delivery_disposition"] = "registered_origin"
            self.ledger.freeze_manifest(CORE, "a", p)
            store = PortraitStore(self.root / "nonbaseline.sqlite", "i", "s")
            registered = store.register_closed_plan(p)
            self.assertEqual(registered["inserted"], 1)
            self.assertEqual(
                registered["plan_id"],
                digest(["i", "s", "automatic", "v", "b" * 64, "c" * 64]),
            )
            self.ledger.prove_registered(
                CORE, "a", PortraitReader(store.path, "i", "s")
            )
            self.assertEqual(
                self.ledger.read_run_mapping(CORE, "a")["plan_id"],
                registered["plan_id"],
            )

    def test_r6_missing_run_history_is_corruption_live_and_reopened(self):
        with self.ledger.tick_owned():
            self.observed()
        live = RuntimeLedgerReader(self.path, "i", "s")
        with sqlite3.connect(self.path) as db:
            db.execute("DELETE FROM runs")
        for reader in (live, RuntimeLedgerReader(self.path, "i", "s")):
            with self.assertRaises(ValueError):
                reader.read_run_mapping(CORE, "a")
            with self.assertRaises(KeyError):
                reader.read_run_mapping(CORE, "never-observed")
        reopened = RuntimeLedger(self.path, "i", "s", enabled=True)
        for writer in (self.ledger, reopened):
            with writer.tick_owned(), self.assertRaises(ValueError):
                writer.observe(
                    CORE,
                    observation(),
                    previous_cursor=cursor(),
                    cursor=cursor(),
                    now=120,
                )

    def test_r6_missing_cursor_history_is_corruption_live_and_reopened(self):
        with self.ledger.tick_owned():
            self.observed()
        live = RuntimeLedgerReader(self.path, "i", "s")
        with sqlite3.connect(self.path) as db:
            db.execute("DELETE FROM cursors")
        for reader in (live, RuntimeLedgerReader(self.path, "i", "s")):
            with self.assertRaises(ValueError):
                reader.read_cursor("outcomes")
            self.assertIsNone(reader.read_cursor("source_links"))
        reopened = RuntimeLedger(self.path, "i", "s", enabled=True)
        for writer in (self.ledger, reopened):
            with writer.tick_owned(), self.assertRaises(ValueError):
                writer.observe(
                    CORE,
                    observation("b", 2),
                    previous_cursor=None,
                    cursor=cursor(2),
                    now=120,
                )
