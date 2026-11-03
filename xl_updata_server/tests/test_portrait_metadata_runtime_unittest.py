"""Automatic metadata coordinator contracts, using unittest only."""

import dataclasses
import hashlib
import importlib.util
import json
import sqlite3
import tempfile
import threading
import unittest
from pathlib import Path
from unittest.mock import patch

from server_app.portraits.runtime_ledger import RuntimeLedger
from server_app.portraits.runtime_ledger_reader import RuntimeLedgerReader
from test_portrait_runtime_ledger_unittest import CORE, cursor, observation


class ResumeTests(unittest.TestCase):
    def test_keyset_page_rejects_history_change_and_replacement(self):
        self.assertTrue(
            hasattr(RuntimeLedgerReader, "read_runs_page"), "bounded resume missing"
        )
        with tempfile.TemporaryDirectory() as directory:
            ledger = RuntimeLedger(Path(directory) / "l.db", "i", "s", enabled=True)
            with ledger.tick_owned():
                ledger.observe(
                    CORE, observation(), previous_cursor=None, cursor=cursor(), now=1
                )
            first = ledger.read_runs_page(limit=1)
            self.assertEqual(["a"], [m["run_id"] for m in first["items"]])
            self.assertEqual(
                [], ledger.read_runs_page(cursor=first["cursor"], limit=1)["items"]
            )
            with ledger.tick_owned():
                ledger.start_source(CORE, "a", now=1)
            with self.assertRaises(ValueError):
                ledger.read_runs_page(cursor=first["cursor"], limit=1)


class EnabledConfigTests(unittest.TestCase):
    def setUp(self):
        from server_app.portraits.metadata_runtime import MetadataConfig

        self.assertIn(
            "updater_data_root",
            {f.name for f in dataclasses.fields(MetadataConfig)},
            "enabled configuration missing",
        )
        from server_app.portraits.core_outcomes import CoreJournal
        from server_app.portraits.executor import ResourcePolicy
        from server_app.portraits.model import digest
        from server_app.portraits.publication import Publication
        from server_app.portraits.source_prepare import SourceRuntime
        from server_app.portraits.store import PortraitStore

        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        updater = self.root / "updater"
        updater.mkdir()
        self.pub = Publication(
            updater,
            instance_id="test",
            source_id="fixed",
            cdn_base="https://fixture.invalid/Android/Bundles",
        )
        self.assertTrue(self.pub.acquire_run())
        self.pub.set_guard(False)
        CoreJournal(self.pub).begin_run("unfinished", trigger="manual")
        self.pub.release_run()
        tool = self.root / "tool"
        tool.mkdir()
        (tool / "tool.py").write_text("# actual inventory\n")
        manifest = (
            ("tool.py", hashlib.sha256((tool / "tool.py").read_bytes()).hexdigest()),
        )
        paths = [
            Path("/usr/bin/python3"),
            Path("/usr/bin/true"),
            Path("/usr/bin/prlimit"),
            Path("/etc/hostname"),
        ]
        runtime = SourceRuntime(
            paths[0],
            Path(__file__).resolve().parents[1],
            tool,
            (),
            paths[1],
            paths[3],
            paths[3],
            paths[2],
            manifest,
            tuple((str(p), hashlib.sha256(p.read_bytes()).hexdigest()) for p in paths),
            "78" * 16,
        )
        self.store = self.root / "store" / "store.db"
        self.store.parent.mkdir()
        PortraitStore(self.store, "test", "fixed")
        self.config = MetadataConfig(
            enabled=True,
            instance_id="test",
            source_id="fixed",
            cdn_base=self.pub.cdn_base,
            updater_data_root=updater,
            prepared_source_root=self.root / "source",
            portrait_root=self.root / "portraits",
            work_root=self.root / "work",
            ledger_path=self.root / "ledger" / "state.db",
            store_path=self.store,
            runtime=runtime,
            tool_fingerprint=digest(dict(manifest)),
            resource_policy=ResourcePolicy(
                self.root / "lease", (self.pub.guard_path,), (self.root,)
            ),
            publication_guards=((self.pub.guard_path, self.pub.scope),),
        )

    def test_enabled_health_is_readonly_and_missing_ledger_stays_missing(self):
        from server_app.portraits.metadata_runtime import MetadataRuntime

        runtime = MetadataRuntime(self.config)
        self.assertFalse(self.config.ledger_path.exists())
        self.assertEqual("uninitialized", runtime.health()["status"])
        self.assertFalse(self.config.ledger_path.exists())


class FlowTests(unittest.TestCase):
    setUp = EnabledConfigTests.setUp

    def promote(self, run="a", version=123, *, unknown=False, bootstrap=False):
        from server_app.portraits.core_outcomes import CoreJournal

        raw = json.dumps(
            {
                "timestamp": version,
                "data": [
                    {"name": name, "hash": char * 32, "size": 2, "ver": 1}
                    for name, char in (("Data", "a"), ("Arts", "b"))
                ],
            }
        ).encode()
        self.assertTrue(self.pub.acquire_run())
        try:
            pin = self.pub.capture(
                raw, version=version, version_file="original.json", bootstrap=bootstrap
            )
            cards = self.pub.stage_cards(version, reason="disabled-cards")
            for relative, value in (
                ("current_version.json", {"version": version}),
                (
                    "character_data/current.json",
                    {
                        "version": version,
                        "characters": {"1": {}},
                        "index": [],
                        "words": {},
                    },
                ),
                (
                    f"versions/{version}/character_data/current.json",
                    {
                        "version": version,
                        "characters": {"1": {}},
                        "index": [],
                        "words": {},
                    },
                ),
                (f"versions/{version}/manifest.json", {"version": version}),
            ):
                path = self.pub.data_root / relative
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text(json.dumps(value))
            context = CoreJournal(self.pub).begin_run(run, trigger="manual")
            context.bind_target(
                version,
                classification="bootstrap" if bootstrap else "processed",
                announcement_eligible=not bootstrap,
            )
            if unknown:
                self.pub.attest_published(version, schedule_enabled=False)
            if bootstrap or unknown:
                ref = self.pub.complete(version)
            context.finish(
                phase="no_change" if bootstrap else "published",
                source_state="pending",
                pin_digest=None if unknown else pin.pin_digest,
                cards=cards,
                bootstrap_ref=ref if bootstrap else None,
                source_recovery=None
                if bootstrap or unknown
                else {"kind": "published_attestation", "schedule_enabled": False},
            )
            return pin
        finally:
            self.pub.release_run()

    def preparation(self, pin, skins=()):
        from server_app.portraits.model import build_catalog, digest
        from server_app.portraits.source_io import MetadataRef
        from server_app.portraits.source_prepare import PreparedSourceRef

        desired = build_catalog(
            "test",
            "fixed",
            pin.version,
            [
                {
                    "family": "cardspine",
                    "character_id": "1",
                    "skin_id": str(skin),
                    "name": str(skin),
                    "config_fingerprint": "a" * 64,
                    "dependency_fingerprint": "b" * 64,
                    "tool_fingerprint": self.config.tool_fingerprint,
                }
                for skin in skins
            ],
        )
        ref = PreparedSourceRef(
            "portrait-prepared-v1",
            "test",
            "fixed",
            pin.version,
            pin.pin_digest,
            self.config.tool_fingerprint,
            MetadataRef(
                digest(["test", "fixed"]), pin.pin_digest, "capture.json", 123, "d" * 64
            ),
            digest(desired),
            len(skins),
            len(skins),
            0,
            0,
        )
        return ref, desired

    def drive(self, runtime, prepared, desired, count=80):
        with (
            patch(
                "server_app.portraits.source_prepare.prepare_source",
                return_value=prepared,
            ),
            patch(
                "server_app.portraits.source_prepare.read_prepared_source",
                return_value={"catalog": desired},
            ),
        ):
            for _ in range(count):
                runtime.tick(now=100)

    def test_same_generation_new_core_token_is_alias(self):
        from server_app.portraits.metadata_runtime import MetadataRuntime

        runtime = MetadataRuntime(self.config)
        prepared, desired = self.preparation(self.promote())
        self.drive(runtime, prepared, desired)
        before = runtime.store_reader.list_plans()["plans"]
        self.promote("b")
        self.drive(runtime, prepared, desired)
        page = runtime.ledger.read_runs_page()
        mappings = {m["run_id"]: m for m in page["items"]}
        self.assertEqual("existing_plan_alias", mappings["b"]["disposition"])
        self.assertEqual(mappings["a"]["origin_core"], mappings["b"]["origin_core"])
        self.assertEqual(before, runtime.store_reader.list_plans()["plans"])

    def test_registered_pending_generation_is_next_comparator(self):
        from server_app.portraits.metadata_runtime import MetadataRuntime

        runtime = MetadataRuntime(self.config)
        self.drive(runtime, *self.preparation(self.promote("base", 123)))
        self.drive(runtime, *self.preparation(self.promote("a", 124), (1,)))
        plans = runtime.store_reader.list_plans()["plans"]
        self.assertEqual(
            1,
            plans[-1]["expected_count"],
            "A new skin was silently treated as another baseline",
        )
        self.assertFalse(plans[-1]["sealed"])
        self.drive(runtime, *self.preparation(self.promote("b", 125), (1,)))
        self.assertEqual(
            0, runtime.store_reader.list_plans()["plans"][-1]["expected_count"]
        )

    def test_registered_and_published_before_ack_restart_repair(self):
        from server_app.portraits.metadata_runtime import MetadataRuntime

        for boundary in ("prove_registered", "ack"):
            with self.subTest(boundary=boundary):
                # One independent scope per fault so the second case is a new baseline.
                if boundary == "ack":
                    self.tmp.cleanup()
                    self.setUp()
                runtime = MetadataRuntime(self.config)
                prepared, desired = self.preparation(self.promote())
                with (
                    patch.object(
                        RuntimeLedger, boundary, side_effect=OSError("fault boundary")
                    ),
                    self.assertRaisesRegex(OSError, "fault boundary"),
                ):
                    self.drive(runtime, prepared, desired)
                self.assertEqual(1, len(runtime.store_reader.list_plans()["plans"]))
                resumed = MetadataRuntime(self.config)
                self.drive(resumed, prepared, desired)
                mapping = resumed.ledger.read_runs_page()["items"][0]
                self.assertEqual("acked", mapping["metadata_phase"])
                self.assertTrue(resumed.store_reader.list_plans()["plans"][0]["sealed"])

    def test_missing_registered_ledger_is_not_reinitialized(self):
        from server_app.portraits.metadata_runtime import MetadataRuntime

        runtime = MetadataRuntime(self.config)
        self.drive(runtime, *self.preparation(self.promote()))
        self.config.ledger_path.rename(self.config.ledger_path.with_name("saved.db"))
        with self.assertRaises(ValueError):
            MetadataRuntime(self.config).tick(now=100)
        self.assertFalse(
            self.config.ledger_path.exists(), "missing ledger was silently recreated"
        )

    def test_same_object_concurrent_tick_defers_without_second_attempt(self):
        from server_app.portraits.metadata_runtime import MetadataRuntime

        runtime = MetadataRuntime(self.config)
        prepared, _desired = self.preparation(self.promote())
        entered, release = threading.Event(), threading.Event()
        errors = []

        def preparing(*args, **kwargs):
            entered.set()
            if not release.wait(5):
                raise AssertionError("barrier was not released")
            return prepared

        def worker():
            try:
                with patch(
                    "server_app.portraits.source_prepare.prepare_source",
                    side_effect=preparing,
                ):
                    for _ in range(30):
                        runtime.tick(now=100)
                        if entered.is_set():
                            break
            except (ValueError, RuntimeError, AssertionError, OSError) as error:
                errors.append(error)

        thread = threading.Thread(target=worker)
        thread.start()
        try:
            self.assertTrue(entered.wait(5))
            self.assertEqual("busy", runtime.tick(now=100)["status"])
        finally:
            release.set()
            thread.join(5)
        self.assertFalse(thread.is_alive())
        self.assertEqual([], errors)
        self.assertEqual(
            1, runtime.ledger.read_runs_page()["items"][0]["prepare_budget"]["attempts"]
        )

    def test_prepare_exhaustion_does_not_rewrite_native_ready(self):
        from server_app.portraits.metadata_runtime import MetadataRuntime
        from server_app.portraits.source_prepare import PreparationError

        runtime = MetadataRuntime(self.config)
        self.promote()
        with patch(
            "server_app.portraits.source_prepare.prepare_source",
            side_effect=PreparationError("fixture invalid"),
        ):
            for step in range(70):
                try:
                    runtime.tick(now=100 + step)
                except PreparationError:
                    pass
        mapping = runtime.ledger.read_runs_page()["items"][0]
        self.assertEqual("preparation_exhausted", mapping["metadata_phase"])
        self.assertEqual(3, mapping["prepare_budget"]["attempts"])
        self.assertEqual("ready", runtime.core.read_source_links()[0]["state"])
        self.assertEqual([], runtime.store_reader.list_plans()["plans"])
        self.assertEqual(
            {
                "max_fetches": 9,
                "max_fetches_per_replay": 3,
                "max_wire_bytes": 3 * 64 * 1024**2,
                "attempt_timeout": 120,
            },
            mapping.get("prepare_policy"),
            "actual transfer policy was not frozen",
        )

    def test_cooperative_failing_fetch_reservations_survive_restart(self):
        from server_app.portraits.executor import Deferred
        from server_app.portraits.metadata_runtime import MetadataRuntime
        from server_app.portraits.source_prepare import PreparationError
        from server_app.portraits.source_transfer import FetchRequired

        runtime = MetadataRuntime(self.config)
        pin = self.promote()
        category = pin.categories[0]
        miss = FetchRequired(
            pin.scope_key,
            pin.pin_digest,
            pin.version,
            f"{category.name.lower()}_{category.hash}.json",
            category.hash,
            category.size,
            "category",
            pin.pin_digest,
        )
        with (
            patch(
                "server_app.portraits.source_prepare.prepare_source", side_effect=miss
            ),
            patch(
                "server_app.portraits.source_transfer.fetch_once",
                side_effect=Deferred("stop during transfer"),
            ) as fetch,
        ):
            for step in range(170):
                previous = fetch.call_count
                try:
                    runtime.tick(now=100 + step)
                except PreparationError:
                    pass
                if fetch.call_count != previous:
                    runtime = MetadataRuntime(self.config)
        self.assertEqual(
            9,
            fetch.call_count,
            "restart/cooperative defer reset global transfer budget",
        )
        mapping = runtime.ledger.read_runs_page()["items"][0]
        self.assertEqual(
            {"fetches": 9, "wire_bytes": 9 * category.size},
            mapping["transfer_reserved"],
        )
        self.assertEqual([], runtime.store_reader.list_plans()["plans"])

    def test_slow_prepare_loses_authority_before_registration(self):
        from server_app.portraits.metadata_runtime import MetadataRuntime

        runtime = MetadataRuntime(self.config)
        prepared, _desired = self.preparation(self.promote())

        def preparing(*args, **kwargs):
            self.promote("b", 124)
            return prepared

        with patch(
            "server_app.portraits.source_prepare.prepare_source", side_effect=preparing
        ):
            for _ in range(30):
                runtime.tick(now=100)
                if (
                    runtime.ledger.read_runs_page()["items"]
                    and runtime.core.latest_promotion()["run_id"] == "b"
                ):
                    break
        for _ in range(20):
            runtime.tick(now=100)
            rows = {m["run_id"]: m for m in runtime.ledger.read_runs_page()["items"]}
            if rows["a"]["metadata_phase"] == "superseded_before_registration":
                break
        self.assertEqual("superseded_before_registration", rows["a"]["metadata_phase"])
        self.assertEqual([], runtime.store_reader.list_plans()["plans"])

    def test_alias_core_promotion_is_effective_comparison_authority(self):
        from server_app.portraits.metadata_runtime import MetadataRuntime

        runtime = MetadataRuntime(self.config)
        original = self.preparation(self.promote("a", 123))
        self.drive(runtime, *original)
        self.drive(runtime, *self.preparation(self.promote("b", 124), (1,)))
        self.promote("alias_a", 123)
        self.drive(runtime, *original)
        self.drive(runtime, *self.preparation(self.promote("c", 125), (1,)), count=120)
        rows = {m["run_id"]: m for m in runtime.ledger.read_runs_page()["items"]}
        frozen = runtime.ledger.read_intent(rows["c"]["core_identity"], "c")
        self.assertEqual(
            "alias_a", frozen["comparison_evidence"]["effective_core"]["run_id"]
        )
        self.assertEqual(1, len(frozen["jobs"]))

    def test_unknown_original_pin_stays_unknown_after_authenticated_resolution(self):
        from server_app.portraits.metadata_runtime import MetadataRuntime

        runtime = MetadataRuntime(self.config)
        pin = self.promote(unknown=True)
        self.drive(runtime, *self.preparation(pin))
        mapping = runtime.ledger.read_runs_page()["items"][0]
        self.assertIsNone(mapping["original_observed"]["pin_digest"])
        self.assertEqual(pin.pin_digest, mapping["pin"]["pin_digest"])
        self.assertEqual("acked", mapping["metadata_phase"])

    def test_first_bootstrap_reuses_request_and_ordinary_noop_creates_no_plan(self):
        from server_app.portraits.core_outcomes import CoreJournal
        from server_app.portraits.metadata_runtime import MetadataRuntime

        runtime = MetadataRuntime(self.config)
        pin = self.promote(bootstrap=True)
        with patch.object(
            runtime.publication,
            "complete",
            side_effect=AssertionError("bootstrap remint"),
        ):
            self.drive(runtime, *self.preparation(pin))
        self.assertTrue(self.pub.acquire_run())
        try:
            context = CoreJournal(self.pub).begin_run("noop", trigger="manual")
            context.bind_target(123, classification="noop", announcement_eligible=False)
            context.finish(phase="no_change", source_state="pending")
        finally:
            self.pub.release_run()
        self.drive(runtime, *self.preparation(pin))
        rows = {m["run_id"]: m for m in runtime.ledger.read_runs_page()["items"]}
        self.assertEqual("ineligible_original_run", rows["noop"]["metadata_phase"])
        self.assertEqual(1, len(runtime.store_reader.list_plans()["plans"]))

    def test_registered_obligation_repairs_after_new_core_without_old_publish(self):
        from server_app.portraits import publisher
        from server_app.portraits.metadata_runtime import MetadataRuntime

        runtime = MetadataRuntime(self.config)
        old = self.preparation(self.promote())
        with (
            patch.object(
                RuntimeLedger,
                "prove_registered",
                side_effect=OSError("crash before proof"),
            ),
            self.assertRaises(OSError),
        ):
            self.drive(runtime, *old)
        newer = self.preparation(self.promote("b", 124), (1,))
        original = publisher._LockedMutation.publish_desired
        published = []

        def record(mutation, catalog, **kwargs):
            published.append(catalog["version"])
            return original(mutation, catalog, **kwargs)

        resumed = MetadataRuntime(self.config)
        with patch.object(publisher._LockedMutation, "publish_desired", record):
            self.drive(resumed, *newer, count=120)
        self.assertEqual(["124"], published)
        rows = {m["run_id"]: m for m in resumed.ledger.read_runs_page()["items"]}
        self.assertEqual("acked", rows["a"]["metadata_phase"])
        self.assertEqual(
            "a",
            resumed.ledger.read_intent(rows["b"]["core_identity"], "b")[
                "comparison_evidence"
            ]["effective_core"]["run_id"],
        )

    def test_corrupt_core_cursor_and_replaced_store_fail_closed(self):
        from server_app.portraits.metadata_runtime import MetadataRuntime
        from server_app.portraits.store import PortraitStore

        runtime = MetadataRuntime(self.config)
        self.drive(runtime, *self.preparation(self.promote()))
        self.store.rename(self.store.with_name("original.db"))
        PortraitStore(self.store, "test", "fixed")
        with self.assertRaises(ValueError):
            for _ in range(8):
                MetadataRuntime(self.config).tick(now=100)

    def test_readonly_health_authenticates_existing_store_proof_after_restart(self):
        from server_app.portraits.metadata_runtime import MetadataRuntime
        from server_app.portraits.store import PortraitStore

        runtime = MetadataRuntime(self.config)
        self.drive(runtime, *self.preparation(self.promote()))
        self.store.rename(self.store.with_name("original.db"))
        PortraitStore(self.store, "test", "fixed")
        with self.assertRaises(ValueError):
            MetadataRuntime(self.config).health()

    def test_health_rechecks_actual_tool_bytes_without_mutating_state(self):
        from server_app.portraits.executor import BoundaryError
        from server_app.portraits.metadata_runtime import MetadataRuntime

        runtime = MetadataRuntime(self.config)
        (self.config.runtime.tool_root / "tool.py").write_text("changed after startup")
        with self.assertRaises(BoundaryError):
            runtime.health()
        self.assertFalse(self.config.ledger_path.exists())

    def test_source_failure_and_failure_link_pending_are_not_zero_success(self):
        from server_app.portraits.core_outcomes import CoreJournal
        from server_app.portraits.metadata_runtime import MetadataRuntime

        runtime = MetadataRuntime(self.config)
        self.promote()
        with patch.object(
            CoreJournal,
            "resolve_pending_source",
            return_value={
                "status": "prerequisite",
                "ref": None,
                "code": "missing_native_pin",
            },
        ):
            for step in range(30):
                runtime.tick(now=100 + step)
                rows = runtime.ledger.read_runs_page()["items"]
                if rows and rows[0]["metadata_phase"] == "source_resolution_exhausted":
                    break
        with patch.object(
            CoreJournal, "fail_source", side_effect=OSError("failure link storage")
        ) as failure:
            for step in range(15):
                runtime.tick(now=200 + step)
            self.assertGreater(failure.call_count, 0)
            self.assertLessEqual(failure.call_count, 15)
        row = runtime.ledger.read_runs_page()["items"][0]
        self.assertEqual("fail_link_pending", row["metadata_phase"])
        self.assertEqual([], runtime.store_reader.list_plans()["plans"])

    def publish_fixture_artifact(self):
        import base64

        from server_app.portraits.publisher import project_accepted
        from server_app.portraits.reader import PortraitReader
        from server_app.portraits.store import PortraitStore
        from test_portrait_store_v2_unittest import result

        raw = base64.b64decode(
            "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+jRZkAAAAASUVORK5CYII="
        )
        sha = hashlib.sha256(raw).hexdigest()
        relative = f"artifacts/{sha}.png"
        path = self.config.portrait_root / relative
        path.parent.mkdir(exist_ok=True)
        path.write_bytes(raw)
        archive = self.config.portrait_root / "archive" / (sha + ".png")
        archive.parent.mkdir(exist_ok=True)
        archive.write_bytes(raw)
        store = PortraitStore(self.store, "test", "fixed")
        reader = PortraitReader(self.store, "test", "fixed")
        pending = [
            p
            for p in reader.list_plans()["plans"]
            if p["expected_count"] and not p["sealed"]
        ]
        self.assertEqual(1, len(pending))
        claim = store.claim(
            "fixture", 100, 30, allowed_plan_ids=[pending[0]["plan_id"]]
        )
        self.assertIsNotNone(claim)
        accepted = result(claim, "success")
        accepted.update(
            artifact_path=relative,
            artifact_sha256=sha,
            archive_path="archive/" + sha + ".png",
            archive_sha256=sha,
            width=1,
            height=1,
            archive_width=1,
            archive_height=1,
        )
        self.assertTrue(
            store.finish(
                claim["job_id"], claim["attempt"], accepted, owner="fixture", now=101
            )
        )
        project_accepted(reader, self.config.portrait_root)
        return relative, sha

    def test_tool_only_comparison_keeps_original_metadata_and_actual_artifact(self):
        from server_app.portraits.metadata_runtime import MetadataRuntime
        from server_app.portraits.model import digest

        runtime = MetadataRuntime(self.config)
        self.drive(runtime, *self.preparation(self.promote("base", 122)))
        self.drive(runtime, *self.preparation(self.promote(), (1,)))
        artifact = self.publish_fixture_artifact()
        oldtool = self.config.tool_fingerprint
        path = self.config.runtime.tool_root / "tool.py"
        path.write_text("# changed actual tool inventory\n")
        manifest = (("tool.py", hashlib.sha256(path.read_bytes()).hexdigest()),)
        self.config = dataclasses.replace(
            self.config,
            runtime=dataclasses.replace(self.config.runtime, tool_manifest=manifest),
            tool_fingerprint=digest(dict(manifest)),
        )
        restarted = MetadataRuntime(self.config)
        self.drive(restarted, *self.preparation(self.promote("b", 124), (1,)))
        rows = {m["run_id"]: m for m in restarted.ledger.read_runs_page()["items"]}
        frozen = restarted.ledger.read_intent(rows["b"]["core_identity"], "b")
        self.assertEqual(1, len(frozen["jobs"]))
        self.assertEqual(oldtool, frozen["comparator"]["skins"][0]["tool_fingerprint"])
        self.assertEqual(
            artifact,
            (
                frozen["comparator"]["skins"][0]["artifact_path"],
                frozen["comparator"]["skins"][0]["artifact_sha256"],
            ),
        )

    def test_changed_removed_blocked_members_keep_configured_ordinals(self):
        from server_app.portraits.metadata_runtime import MetadataRuntime
        from server_app.portraits.model import build_catalog, digest
        from server_app.portraits.publisher import read_catalog

        runtime = MetadataRuntime(self.config)
        self.drive(runtime, *self.preparation(self.promote(), (1, 2)))
        prepared, desired = self.preparation(self.promote("b", 124), (2, 3))
        rows = desired["skins"]
        rows[0]["config_fingerprint"] = "e" * 64
        rows[1].update(state="blocked", diagnostics=["missing configured prefab"])
        desired = build_catalog("test", "fixed", "124", rows)
        prepared = dataclasses.replace(
            prepared, snapshot_digest=digest(desired), available=1, blocked=1
        )
        self.drive(runtime, prepared, desired)
        plans = runtime.store_reader.list_plans()["plans"]
        manifest = runtime.store_reader.read_manifest(plans[-1]["plan_id"])
        self.assertEqual(
            {("2", "changed_inputs"), ("3", "new_skin")},
            {(j["skin_id"], j["reason"]) for j in manifest["jobs"]},
        )
        catalog = read_catalog(self.config.portrait_root)
        self.assertEqual(
            [("2", 1), ("3", 2)],
            [(r["skin_id"], r["ordinal"]) for r in catalog["skins"]],
        )
        self.assertEqual("blocked", catalog["skins"][1]["state"])

    def test_projection_between_freeze_and_commit_preserves_actual_image(self):
        from server_app.portraits.metadata_runtime import MetadataRuntime
        from server_app.portraits.publisher import read_catalog

        runtime = MetadataRuntime(self.config)
        self.drive(runtime, *self.preparation(self.promote("base", 122)))
        self.drive(runtime, *self.preparation(self.promote(), (1,)))
        original = runtime._stage
        artifacts = []

        def project_after_stage(mapping):
            original(mapping)
            artifacts.append(self.publish_fixture_artifact())

        with patch.object(runtime, "_stage", side_effect=project_after_stage):
            self.drive(runtime, *self.preparation(self.promote("b", 124), (1,)))
        self.assertEqual(1, len(artifacts))
        row = read_catalog(self.config.portrait_root)["skins"][0]
        self.assertEqual(artifacts[0], (row["artifact_path"], row["artifact_sha256"]))

    def test_core_prefix_corruption_fails_without_ledger_reset(self):
        from server_app.portraits.metadata_runtime import MetadataRuntime

        runtime = MetadataRuntime(self.config)
        self.drive(runtime, *self.preparation(self.promote()))
        before = runtime.ledger.read_fence()
        with sqlite3.connect(runtime.core.path) as db:
            db.execute("UPDATE outcomes SET prefix=?", ("f" * 64,))
        with self.assertRaises(ValueError):
            MetadataRuntime(self.config).tick(now=100)
        self.assertEqual(before, runtime.ledger.read_fence())

    def test_latest_core_rechecked_under_publisher_then_nonblocking_raw_owner(self):
        import fcntl

        from server_app.portraits.metadata_runtime import MetadataRuntime

        runtime = MetadataRuntime(self.config)
        prepared, desired = self.preparation(self.promote())
        with (
            patch(
                "server_app.portraits.source_prepare.prepare_source",
                return_value=prepared,
            ),
            patch(
                "server_app.portraits.source_prepare.read_prepared_source",
                return_value={"catalog": desired},
            ),
        ):
            for _ in range(40):
                runtime.tick(now=100)
                rows = runtime.ledger.read_runs_page()["items"]
                if rows and rows[0]["metadata_phase"] == "manifest_frozen":
                    break
        self.assertEqual("manifest_frozen", rows[0]["metadata_phase"])
        acquire = runtime.publication.acquire_run
        calls = []

        def supersede_at_raw_acquisition():
            with (
                (self.config.portrait_root / ".publisher.lock").open("rb") as stream,
                self.assertRaises(BlockingIOError),
            ):
                fcntl.flock(stream.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
            self.promote("newer", 124)
            calls.append(1)
            return acquire()

        with patch.object(
            runtime.publication, "acquire_run", side_effect=supersede_at_raw_acquisition
        ):
            for _ in range(10):
                runtime.tick(now=100)
                if calls:
                    break
        self.assertEqual([1], calls)
        self.assertEqual([], runtime.store_reader.list_plans()["plans"])
        self.assertEqual(
            "superseded_before_registration",
            runtime.ledger.read_runs_page()["items"][0]["metadata_phase"],
        )

    def test_transfer_reservation_immutable_policy_and_byte_bound(self):
        from server_app.portraits.executor import Deferred
        from server_app.portraits.metadata_runtime import MetadataRuntime

        runtime = MetadataRuntime(self.config)
        self.promote()
        with patch(
            "server_app.portraits.source_prepare.prepare_source",
            side_effect=Deferred("stop"),
        ):
            for _ in range(30):
                runtime.tick(now=100)
        mapping = runtime.ledger.read_runs_page()["items"][0]
        identity = mapping["core_identity"]
        with runtime.ledger.tick_owned():
            with self.assertRaises(ValueError):
                runtime.ledger.freeze_prepare_policy(
                    identity, "a", dict(mapping["prepare_policy"], max_fetches=8)
                )
            self.assertTrue(runtime.ledger.start_prepare(identity, "a", now=101))
            self.assertTrue(
                runtime.ledger.reserve_prepare_fetch(
                    identity, "a", wire_size=128 * 1024**2
                )
            )
            self.assertFalse(
                runtime.ledger.reserve_prepare_fetch(
                    identity, "a", wire_size=128 * 1024**2
                )
            )
        reopened = RuntimeLedgerReader(self.config.ledger_path, "test", "fixed")
        self.assertEqual(
            {"fetches": 1, "wire_bytes": 128 * 1024**2},
            reopened.read_run_mapping(identity, "a")["transfer_reserved"],
        )

    def test_busy_raw_owner_does_not_report_registration_committed(self):
        from server_app.portraits.metadata_runtime import MetadataRuntime

        runtime = MetadataRuntime(self.config)
        prepared, desired = self.preparation(self.promote())
        with (
            patch(
                "server_app.portraits.source_prepare.prepare_source",
                return_value=prepared,
            ),
            patch(
                "server_app.portraits.source_prepare.read_prepared_source",
                return_value={"catalog": desired},
            ),
        ):
            for _ in range(40):
                runtime.tick(now=100)
                rows = runtime.ledger.read_runs_page()["items"]
                if rows and rows[0]["metadata_phase"] == "manifest_frozen":
                    break
        self.assertTrue(self.pub.acquire_run())
        try:
            statuses = [runtime.tick(now=100)["status"] for _ in range(8)]
        finally:
            self.pub.release_run()
        self.assertIn("raw_busy", statuses)
        self.assertNotIn("committed", statuses)
        self.assertEqual([], runtime.store_reader.list_plans()["plans"])

    def test_real_core_registration_baseline_and_restart(self):
        from server_app.portraits.metadata_runtime import MetadataRuntime
        from server_app.portraits.model import digest
        from server_app.portraits.publisher import read_catalog
        from server_app.portraits.reader import PortraitReader
        from server_app.portraits.source_io import MetadataRef
        from server_app.portraits.source_prepare import PreparedSourceRef

        pin = self.promote()
        desired = {
            "schema_version": 1,
            "instance_id": "test",
            "source_id": "fixed",
            "version": "123",
            "skins": [],
        }
        prepared = PreparedSourceRef(
            "portrait-prepared-v1",
            "test",
            "fixed",
            "123",
            pin.pin_digest,
            self.config.tool_fingerprint,
            MetadataRef(
                digest(["test", "fixed"]), pin.pin_digest, "capture.json", 123, "d" * 64
            ),
            "b" * 64,
            0,
            0,
            0,
            0,
        )
        runtime = MetadataRuntime(self.config)
        with (
            patch(
                "server_app.portraits.source_prepare.prepare_source",
                return_value=prepared,
            ),
            patch(
                "server_app.portraits.source_prepare.read_prepared_source",
                return_value={"catalog": desired},
            ),
        ):
            for _ in range(40):
                runtime.tick(now=100)
        self.assertTrue(
            self.config.ledger_path.exists(),
            "automatic metadata tick did not persist observation",
        )
        plans = PortraitReader(self.store, "test", "fixed").list_plans()["plans"]
        self.assertEqual(1, len(plans))
        self.assertTrue(plans[0]["sealed"])
        self.assertEqual(0, plans[0]["expected_count"])
        self.assertEqual("123", read_catalog(self.config.portrait_root)["version"])
        restarted = MetadataRuntime(self.config)
        for _ in range(12):
            restarted.tick(now=101)
        self.assertEqual(
            plans, PortraitReader(self.store, "test", "fixed").list_plans()["plans"]
        )

    def test_wrong_guard_scope_fails_before_creating_runtime_storage(self):
        from server_app.portraits.metadata_runtime import MetadataRuntime

        self.pub.guard_path.write_text(
            json.dumps({"schema": 1, "scope": "f" * 64, "updating": False})
        )
        with self.assertRaises(ValueError):
            MetadataRuntime(self.config)
        self.assertFalse(self.config.ledger_path.exists())

    def test_overlap_and_changed_tool_inventory_fail_before_state(self):
        from server_app.portraits.metadata_runtime import MetadataRuntime

        with self.assertRaises(ValueError):
            MetadataRuntime(
                dataclasses.replace(self.config, work_root=self.config.portrait_root)
            )
        (self.config.runtime.tool_root / "tool.py").write_text("changed\n")
        from server_app.portraits.executor import BoundaryError

        with self.assertRaises(BoundaryError):
            MetadataRuntime(self.config)
        self.assertFalse(self.config.ledger_path.exists())


class DisabledRuntimeTests(unittest.TestCase):
    def test_default_disabled_tick_and_health_create_nothing(self):
        spec = importlib.util.find_spec("server_app.portraits.metadata_runtime")
        self.assertIsNotNone(spec, "automatic metadata runtime is missing")
        from server_app.portraits.metadata_runtime import (
            MetadataConfig,
            MetadataRuntime,
        )

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            runtime = MetadataRuntime(MetadataConfig())
            self.assertEqual({"enabled": False}, runtime.health())
            self.assertEqual({"enabled": False}, runtime.tick(now=100))
            self.assertEqual([], list(root.iterdir()))
