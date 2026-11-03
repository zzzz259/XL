"""Execution ownership contracts; unittest only, real SQLite authorities."""

import importlib.util
import tempfile
import threading
import unittest
from contextlib import contextmanager
from pathlib import Path
from unittest.mock import patch

import test_portrait_metadata_runtime_unittest as metadata_fixture
from server_app.portraits.reader import PortraitReader
from server_app.portraits.source_io import MetadataRef
from server_app.portraits.store import PortraitStore
from test_portrait_store_v2_unittest import plan, result


class ImmutableJobTests(unittest.TestCase):
    def test_global_lookup_rejects_mutable_fields_and_oversize_payload(self):
        import json
        import sqlite3

        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "q.db"
            store = PortraitStore(path, "i", "s")
            original = plan()
            store.register_closed_plan(original)
            reader = PortraitReader(path, "i", "s")
            job = original["jobs"][0]
            for invalid in (
                dict(job, attempt=1),
                dict(job, unexpected="x" * (1024 * 1024)),
            ):
                with sqlite3.connect(path) as db:
                    db.execute("UPDATE jobs SET payload=?", (json.dumps(invalid),))
                with self.assertRaises(ValueError):
                    reader.read_job(job["job_id"])

    def test_status_totals_are_readonly(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "q.db"
            store = PortraitStore(path, "i", "s")
            store.register_closed_plan(plan())
            reader = PortraitReader(path, "i", "s")
            self.assertTrue(
                hasattr(reader, "read_status"), "missing bounded status totals"
            )
            before = path.read_bytes()
            self.assertEqual(reader.read_status()["jobs"]["pending"], 1)
            self.assertEqual(reader.read_status()["plans"]["unsealed"], 1)
            self.assertEqual(path.read_bytes(), before)

    def test_read_original_job_after_claim_and_manual_transfer(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "store.db"
            store = PortraitStore(path, "i", "s")
            original = plan()
            pid = store.register_closed_plan(original)["plan_id"]
            reader = PortraitReader(path, "i", "s")
            self.assertTrue(hasattr(reader, "read_job"), "missing immutable job lookup")
            claim = store.claim("owner", 1, allowed_plan_ids=[pid])
            self.assertEqual(reader.read_job(claim["job_id"]), original["jobs"][0])
            store.finish(
                claim["job_id"], claim["attempt"], result(claim), owner="owner", now=2
            )
            store.seal_plan(pid)
            store.register_closed_plan(plan(manual="retry"))
            self.assertEqual(reader.read_job(claim["job_id"]), original["jobs"][0])
            with self.assertRaises(KeyError):
                reader.read_job("f" * 64)


if __name__ == "__main__":
    unittest.main()


class RenewalTests(unittest.TestCase):
    def api(self):
        self.assertIsNotNone(
            importlib.util.find_spec("server_app.portraits.execution_lease"),
            "missing renewal helper",
        )
        from server_app.portraits.execution_lease import LeaseRenewer

        return LeaseRenewer

    def test_finish_waits_for_inflight_renewal_then_final_fence(self):
        cls = self.api()
        entered, release = threading.Event(), threading.Event()
        calls = []

        def renew():
            calls.append(threading.get_ident())
            if len(calls) == 1:
                entered.set()
                self.assertTrue(release.wait(2))
            return True

        helper = cls(renew, interval=0.01)
        helper.start()
        self.assertTrue(entered.wait(2))
        release.set()
        self.assertTrue(helper.final_fence())
        self.assertFalse(helper.thread.is_alive())
        self.assertGreaterEqual(len(calls), 2)
        self.assertFalse(helper.lost.is_set())
        self.assertEqual(calls[-1], threading.get_ident())

    def test_uncertain_renewal_cancels_and_no_final_acceptance(self):
        cls = self.api()
        entered = threading.Event()

        def renew():
            entered.set()
            raise OSError("uncertain commit")

        helper = cls(renew, interval=0.01)
        helper.start()
        self.assertTrue(entered.wait(2))
        helper.stop()
        self.assertTrue(helper.lost.is_set())
        self.assertFalse(helper.final_fence())

    def test_lost_fence_blocks_finish(self):
        helper = self.api()(lambda: False, interval=1)
        self.assertFalse(helper.final_fence())
        self.assertTrue(helper.lost.is_set())


class RuntimeTests(unittest.TestCase):
    def test_configuration_rejects_nonfinite_or_unsafe_renew_period(self):
        from server_app.portraits.execution_runtime import ExecutionConfig

        for kwargs in (
            {"job_seconds": float("inf")},
            {"lease_seconds": 0},
            {"renew_interval": 60},
            {"owner": ""},
        ):
            with self.subTest(kwargs=kwargs), self.assertRaises(ValueError):
                ExecutionConfig(**kwargs)

    def test_cli_disabled_and_help_do_not_load_configuration(self):
        self.assertIsNotNone(
            importlib.util.find_spec("server_app.portraits.cli"), "missing runnable CLI"
        )
        from server_app.portraits.cli import main

        self.assertEqual(main(["tick", "--config", "/absent/config.json"]), 0)
        with self.assertRaises(SystemExit) as exit:
            main(["--help"])
        self.assertEqual(exit.exception.code, 0)

    def test_disabled_runtime_creates_no_authority(self):
        self.assertIsNotNone(
            importlib.util.find_spec("server_app.portraits.execution_runtime"),
            "missing execution runtime",
        )
        from server_app.portraits.execution_runtime import (
            ExecutionConfig,
            PortraitRuntime,
        )

        runtime = PortraitRuntime(ExecutionConfig())
        self.assertEqual(runtime.tick(), {"enabled": False})
        self.assertEqual(runtime.health(), {"enabled": False})

    def test_recovery_required_finishes_without_renderer(self):
        self.assertIsNotNone(
            importlib.util.find_spec("server_app.portraits.execution_runtime"),
            "missing execution runtime",
        )
        from server_app.portraits.execution_runtime import finish_claim

        with tempfile.TemporaryDirectory() as tmp:
            store = PortraitStore(Path(tmp) / "q.db", "i", "s")
            pid = store.register_closed_plan(plan())["plan_id"]
            claim = store.claim("owner", 1001, allowed_plan_ids=[pid])
            self.assertTrue(claim["recovery_required"])

            class Slot:
                process = None
                poisoned = False

            def forbidden(*args):
                self.fail("recovery called renderer")

            self.assertEqual(
                finish_claim(
                    store,
                    claim,
                    Slot(),
                    forbidden,
                    clock=lambda: 1002,
                    lease_seconds=60,
                    renew_interval=10,
                    stop_event=threading.Event(),
                ),
                "finished",
            )
            self.assertEqual(store.seal_plan(pid)["counts"]["failed"], 1)

    def test_confirmed_stop_defers_but_cleanup_unknown_retains_running(self):
        from server_app.portraits.execution_runtime import finish_claim
        from server_app.portraits.executor import CleanupError, Deferred

        with tempfile.TemporaryDirectory() as tmp:
            store = PortraitStore(Path(tmp) / "q.db", "i", "s")
            pid = store.register_closed_plan(plan())["plan_id"]
            claim = store.claim("owner", 1, allowed_plan_ids=[pid])

            class Slot:
                process = None
                poisoned = False

            stop = threading.Event()
            stop.set()

            def cancelled(cancel):
                raise Deferred("stop")

            self.assertEqual(
                finish_claim(
                    store,
                    claim,
                    Slot(),
                    cancelled,
                    clock=lambda: 2,
                    lease_seconds=60,
                    renew_interval=10,
                    stop_event=stop,
                ),
                "deferred",
            )
            claim = store.claim("owner", 3, allowed_plan_ids=[pid])
            slot = Slot()
            slot.poisoned = True
            with self.assertRaises(CleanupError):
                finish_claim(
                    store,
                    claim,
                    slot,
                    cancelled,
                    clock=lambda: 4,
                    lease_seconds=60,
                    renew_interval=10,
                    stop_event=stop,
                )
            self.assertEqual(store.list_events(), [])
            self.assertEqual(
                PortraitReader(store.path, "i", "s").read_plan_snapshot(pid)["counts"][
                    "running"
                ],
                1,
            )

    def test_expired_final_fence_never_accepts_candidate(self):
        from server_app.portraits.execution_runtime import finish_claim

        with tempfile.TemporaryDirectory() as tmp:
            store = PortraitStore(Path(tmp) / "q.db", "i", "s")
            pid = store.register_closed_plan(plan())["plan_id"]
            claim = store.claim("owner", 1, 10, allowed_plan_ids=[pid])

            class Slot:
                process = None
                poisoned = False

            status = finish_claim(
                store,
                claim,
                Slot(),
                lambda cancel: result(claim),
                clock=lambda: 12,
                lease_seconds=60,
                renew_interval=10,
                stop_event=threading.Event(),
            )
            self.assertEqual(status, "ownership_lost")
            self.assertEqual(store.list_events(), [])


class CompositionTests(unittest.TestCase):
    setUp = metadata_fixture.FlowTests.setUp
    promote = metadata_fixture.FlowTests.promote
    preparation = metadata_fixture.FlowTests.preparation
    drive = metadata_fixture.FlowTests.drive

    def test_existing_store_init_is_readonly(self):
        from server_app.portraits.execution_runtime import initialize_store

        before = sorted(
            str(p.relative_to(self.root))
            for p in self.root.rglob("*")
            if not str(p).endswith(("-wal", "-shm"))
        )
        self.assertEqual(initialize_store(self.config), "existing")
        self.assertEqual(
            sorted(
                str(p.relative_to(self.root))
                for p in self.root.rglob("*")
                if not str(p).endswith(("-wal", "-shm"))
            ),
            before,
        )

    def test_manual_requires_real_automatic_baseline(self):
        from server_app.portraits.execution_manual import manual_request
        from server_app.portraits.execution_runtime import (
            ExecutionConfig,
            PortraitRuntime,
        )

        runtime = PortraitRuntime(ExecutionConfig(metadata=self.config))
        with self.assertRaisesRegex(ValueError, "automatic baseline required"):
            manual_request(runtime, {}, now=101)

    def test_registered_claim_finish_projection_and_seal(self):
        from server_app.portraits.execution_runtime import (
            ExecutionConfig,
            PortraitRuntime,
            failure,
        )

        runtime = PortraitRuntime(ExecutionConfig(metadata=self.config))
        self.drive(runtime.metadata, *self.preparation(self.promote("base", 123)))
        self.drive(runtime.metadata, *self.preparation(self.promote("a", 124), (1,)))
        pid = runtime.metadata.store_reader.list_plans()["plans"][-1]["plan_id"]
        self.assertTrue(hasattr(runtime, "execute_tick"), "missing composed execution")
        with patch.object(
            runtime,
            "_render",
            side_effect=lambda slot, claim, manifest, cancel: failure(
                claim, "fixture failure"
            ),
        ):
            outcome = runtime.execute_tick([pid], now=lambda: 101)
        self.assertEqual(outcome["status"], "finished")
        self.assertEqual(
            runtime.metadata.store_reader.read_plan_snapshot(pid)["counts"]["failed"], 1
        )
        self.assertTrue(runtime.metadata.store_reader.read_plan_snapshot(pid)["sealed"])
        self.assertEqual(len(runtime.metadata.store_reader.read_events()["events"]), 1)

    def test_admission_failure_does_not_claim(self):
        from server_app.portraits.execution_runtime import (
            ExecutionConfig,
            PortraitRuntime,
        )

        runtime = PortraitRuntime(ExecutionConfig(metadata=self.config))
        self.drive(runtime.metadata, *self.preparation(self.promote("base", 123)))
        self.drive(runtime.metadata, *self.preparation(self.promote("a", 124), (1,)))
        pid = runtime.metadata.store_reader.list_plans()["plans"][-1]["plan_id"]
        self.assertTrue(hasattr(runtime, "execute_tick"), "missing composed execution")
        from server_app.portraits.executor import Deferred

        with patch(
            "server_app.portraits.executor.ResourceSlot.__enter__",
            side_effect=Deferred("resource"),
        ):
            outcome = runtime.execute_tick([pid], now=lambda: 101)
        self.assertEqual(outcome["status"], "deferred")
        claim = PortraitStore(self.store, "test", "fixed").claim(
            "later", 102, allowed_plan_ids=[pid]
        )
        self.assertEqual(claim["attempt"], 1)

    def test_lazy_replay_renders_before_package_exit(self):
        import time

        from server_app.portraits.execution_runtime import (
            ExecutionConfig,
            PortraitRuntime,
        )

        runtime = PortraitRuntime(ExecutionConfig(metadata=self.config))
        self.drive(runtime.metadata, *self.preparation(self.promote("base", 123)))
        prepared, desired = self.preparation(self.promote("a", 124), (1,))
        self.drive(runtime.metadata, prepared, desired)
        pid = runtime.metadata.store_reader.list_plans()["plans"][-1]["plan_id"]
        manifest = runtime._manifest(pid)
        claim = dict(
            manifest["jobs"][0], attempt=1, recovery={"deadline": time.time() + 300}
        )
        active = []

        @contextmanager
        def package(*args, **kwargs):
            active.append(True)
            try:
                yield "live-package"
            finally:
                active.clear()

        def render(slot, job, ref, **kwargs):
            self.assertEqual(active, [True])
            self.assertEqual(ref, "live-package")
            self.assertLessEqual(kwargs["timeout_seconds"], 120)
            return {"candidate": "inside"}

        document = {
            "catalog": desired,
            "source_snapshot": {
                "skins": [
                    {
                        "character_id": "1",
                        "skin_id": "1",
                        "lazy_input": {"frozen": True},
                    }
                ]
            },
        }
        with (
            patch(
                "server_app.portraits.source_prepare.read_prepared_source",
                return_value=document,
            ),
            patch(
                "server_app.portraits.package_prepare.prepare_lazy_package",
                side_effect=package,
            ),
            patch(
                "server_app.portraits.render_prepare.render_candidate",
                side_effect=render,
            ),
        ):
            self.assertEqual(
                runtime._render(
                    object(),
                    claim,
                    MetadataRef(**manifest["core_publication_ref"]),
                    threading.Event(),
                ),
                {"candidate": "inside"},
            )
        self.assertFalse(active)

    def test_blocked_registered_member_never_opens_package(self):
        import time

        from server_app.portraits.execution_runtime import (
            ExecutionConfig,
            PortraitRuntime,
        )

        runtime = PortraitRuntime(ExecutionConfig(metadata=self.config))
        self.drive(runtime.metadata, *self.preparation(self.promote("base", 123)))
        prepared, desired = self.preparation(self.promote("a", 124), (1,))
        self.drive(runtime.metadata, prepared, desired)
        pid = runtime.metadata.store_reader.list_plans()["plans"][-1]["plan_id"]
        manifest = runtime._manifest(pid)
        claim = dict(
            manifest["jobs"][0], attempt=1, recovery={"deadline": time.time() + 300}
        )
        desired["skins"][0]["state"] = "blocked"
        with (
            patch(
                "server_app.portraits.source_prepare.read_prepared_source",
                return_value={"catalog": desired},
            ),
            patch(
                "server_app.portraits.package_prepare.prepare_lazy_package"
            ) as package,
        ):
            self.assertEqual(
                runtime._render(
                    object(),
                    claim,
                    MetadataRef(**manifest["core_publication_ref"]),
                    threading.Event(),
                )["status"],
                "blocked",
            )
        package.assert_not_called()

    def test_explicit_first_store_init_and_missing_history_rejection(self):
        import server_app.portraits.execution_runtime as execution

        self.assertTrue(
            hasattr(execution, "initialize_store"),
            "missing first-install Store bootstrap",
        )
        for path in self.store.parent.iterdir():
            path.unlink()
        self.assertEqual(execution.initialize_store(self.config), "initialized")
        self.assertEqual(execution.initialize_store(self.config), "existing")
        for path in self.store.parent.iterdir():
            path.unlink()
        self.config.ledger_path.parent.mkdir()
        self.config.ledger_path.write_text("retained evidence")
        with self.assertRaisesRegex(ValueError, "existing runtime evidence"):
            execution.initialize_store(self.config)
        self.assertFalse(self.store.exists())

    def test_manual_history_dryrun_idempotency_and_current_unchanged(self):
        self.assertIsNotNone(
            importlib.util.find_spec("server_app.portraits.execution_manual"),
            "missing explicit manual history",
        )
        from server_app.portraits.execution_manual import manual_request
        from server_app.portraits.execution_runtime import (
            ExecutionConfig,
            PortraitRuntime,
        )
        from server_app.portraits.publisher import read_catalog

        runtime = PortraitRuntime(ExecutionConfig(metadata=self.config))
        old_ref, old_desired = self.preparation(self.promote("old", 123), (1, 2))
        self.drive(runtime.metadata, old_ref, old_desired)
        old_manifest = runtime.metadata.store_reader.read_manifest(
            runtime.metadata.store_reader.list_plans()["plans"][0]["plan_id"]
        )
        self.drive(
            runtime.metadata, *self.preparation(self.promote("current", 124), (1, 2))
        )
        before = read_catalog(self.config.portrait_root)
        import dataclasses

        intent = {
            "request_id": "history-1",
            "version": "123",
            "characters": ["1"],
            "skins": ["2"],
            "prepared_ref": dataclasses.asdict(old_ref),
            "request_ref": old_manifest["core_publication_ref"],
        }
        with patch(
            "server_app.portraits.source_prepare.read_prepared_source",
            return_value={"catalog": old_desired},
        ):
            preview = manual_request(runtime, intent, dry_run=True, now=101)
            self.assertEqual(preview["expected_jobs"], 1)
            self.assertEqual(
                len(runtime.metadata.store_reader.list_plans()["plans"]), 2
            )
            registered = manual_request(runtime, intent, now=101)
            self.assertEqual(manual_request(runtime, intent, now=102), registered)
            with self.assertRaisesRegex(ValueError, "manual request conflict"):
                manual_request(runtime, dict(intent, skins=["1"]), now=102)
        self.assertEqual(before, read_catalog(self.config.portrait_root))
        manifest = runtime.metadata.store_reader.read_manifest(registered["plan_id"])
        self.assertEqual(manifest["delivery_disposition"], "manual_query_only")
        self.assertEqual(manifest["jobs"][0]["reason"], "manual_backfill")
        from server_app.portraits.execution_runtime import failure

        with patch.object(
            runtime,
            "_render",
            side_effect=lambda slot, claim, manifest, cancel: failure(claim, "fixture"),
        ):
            outcome = runtime.execute_tick(
                [registered["plan_id"]], now=lambda: 103, manual_request_id="history-1"
            )
        self.assertEqual(outcome["status"], "finished")

    def test_tick_dynamically_executes_registered_work_after_metadata_idle(self):
        from server_app.portraits.execution_runtime import (
            ExecutionConfig,
            PortraitRuntime,
        )

        runtime = PortraitRuntime(ExecutionConfig(metadata=self.config))
        self.drive(runtime.metadata, *self.preparation(self.promote("base", 123)))
        self.drive(runtime.metadata, *self.preparation(self.promote("a", 124), (1,)))
        pid = runtime.metadata.store_reader.list_plans()["plans"][-1]["plan_id"]
        with patch.object(
            runtime, "execute_tick", return_value={"status": "executed"}
        ) as execute:
            for _ in range(5):
                outcome = runtime.tick()
                if outcome.get("status") == "executed":
                    break
        self.assertEqual(outcome["status"], "executed")
        execute.assert_called_once_with([pid])

    def test_five_attempts_wait_three_interruptibly(self):
        from server_app.portraits.execution_runtime import (
            ExecutionConfig,
            PortraitRuntime,
        )
        from server_app.portraits.executor import Deferred

        runtime = PortraitRuntime(ExecutionConfig(metadata=self.config))
        self.drive(runtime.metadata, *self.preparation(self.promote("base", 123)))
        self.drive(runtime.metadata, *self.preparation(self.promote("a", 124), (1,)))
        pid = runtime.metadata.store_reader.list_plans()["plans"][-1]["plan_id"]
        waits = []

        class Stop:
            def is_set(self):
                return bool(waits)

            def wait(self, seconds):
                waits.append(seconds)
                return True

        runtime.stop_event = Stop()
        with patch.object(
            runtime, "_render", side_effect=Deferred("resource")
        ) as render:
            for _ in range(5):
                self.assertEqual(
                    runtime.execute_tick([pid], now=lambda: 101)["status"], "deferred"
                )
            self.assertEqual(render.call_count, 5)
        self.assertEqual(waits, [3])
        self.assertEqual(runtime.execute_tick([pid], now=lambda: 101)["status"], "stop")

    def test_manual_reuses_sealed_automatic_original_payload(self):
        import dataclasses

        from server_app.portraits.execution_manual import manual_request
        from server_app.portraits.execution_runtime import (
            ExecutionConfig,
            PortraitRuntime,
            failure,
        )

        runtime = PortraitRuntime(ExecutionConfig(metadata=self.config))
        self.drive(runtime.metadata, *self.preparation(self.promote("base", 123)))
        prepared, desired = self.preparation(self.promote("a", 124), (1,))
        self.drive(runtime.metadata, prepared, desired)
        reader = runtime.metadata.store_reader
        pid = reader.list_plans()["plans"][-1]["plan_id"]
        original = reader.read_manifest(pid)
        with patch.object(
            runtime,
            "_render",
            side_effect=lambda slot, claim, manifest, cancel: failure(claim, "fixture"),
        ):
            runtime.execute_tick([pid], now=lambda: 101)
        final = reader.read_plan_snapshot(pid)
        intent = {
            "request_id": "retry-sealed",
            "version": "124",
            "characters": "all",
            "skins": "all",
            "prepared_ref": dataclasses.asdict(prepared),
            "request_ref": original["core_publication_ref"],
        }
        with patch(
            "server_app.portraits.source_prepare.read_prepared_source",
            return_value={"catalog": desired},
        ):
            manual = manual_request(runtime, intent, now=102)
        self.assertEqual(
            reader.read_manifest(manual["plan_id"])["jobs"], original["jobs"]
        )
        self.assertEqual(reader.read_plan_snapshot(pid), final)

    def test_finish_before_projection_restarts_without_new_event(self):
        from server_app.portraits.execution_runtime import (
            ExecutionConfig,
            PortraitRuntime,
            failure,
        )

        runtime = PortraitRuntime(ExecutionConfig(metadata=self.config))
        self.drive(runtime.metadata, *self.preparation(self.promote("base", 123)))
        self.drive(runtime.metadata, *self.preparation(self.promote("a", 124), (1,)))
        pid = runtime.metadata.store_reader.list_plans()["plans"][-1]["plan_id"]
        project = runtime._project
        calls = []

        def fault():
            calls.append(1)
            if len(calls) == 2:
                raise OSError("projection interrupted")
            return project()

        with (
            patch.object(runtime, "_project", side_effect=fault),
            patch.object(
                runtime,
                "_render",
                side_effect=lambda slot, claim, manifest, cancel: failure(
                    claim, "fixture"
                ),
            ),
            self.assertRaises(OSError),
        ):
            runtime.execute_tick([pid], now=lambda: 101)
        self.assertTrue(
            hasattr(runtime, "_read_checkpoint"),
            "missing durable projection checkpoint",
        )
        self.assertEqual(runtime._read_checkpoint()["position"], 0)
        restarted = PortraitRuntime(ExecutionConfig(metadata=self.config))
        self.assertEqual(restarted._project(), 1)
        self.assertEqual(restarted._read_checkpoint()["position"], 1)
        self.assertEqual(restarted._project(), 0)
        self.assertEqual(
            len(restarted.metadata.store_reader.read_events()["events"]), 1
        )
        self.assertEqual(restarted.health()["execution"]["event_cursor"]["position"], 1)
