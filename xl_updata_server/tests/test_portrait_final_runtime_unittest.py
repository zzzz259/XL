"""Final cross-component regressions with actual durable producer/consumer state."""

import contextlib
import io
import unittest
from unittest.mock import patch

import test_portrait_metadata_runtime_unittest as fixtures
from server_app.portraits.core_outcomes import CoreJournal
from server_app.portraits.execution_runtime import ExecutionConfig, PortraitRuntime
from server_app.portraits.metadata_runtime import MetadataRuntime
from server_app.portraits.runtime_ledger_reader import RuntimeLedgerReader
from server_app.portraits.source_prepare import PreparationError


class FinalRuntimeTests(unittest.TestCase):
    setUp = fixtures.EnabledConfigTests.setUp
    promote = fixtures.FlowTests.promote
    preparation = fixtures.FlowTests.preparation
    drive = fixtures.FlowTests.drive

    def identity(self, runtime):
        cursor = runtime.ledger.read_cursor("outcomes")
        return {key: cursor[key] for key in ("scope", "journal_id", "file_id")}

    def noop(self, run):
        self.assertTrue(self.pub.acquire_run())
        try:
            context = CoreJournal(self.pub).begin_run(run, trigger="scheduled")
            context.bind_target(123, classification="noop", announcement_eligible=False)
            context.finish(phase="no_change", source_state="not_requested")
        finally:
            self.pub.release_run()

    def test_unbound_failed_and_recovered_interrupted_survive_restart(self):
        self.assertTrue(self.pub.acquire_run())
        try:
            journal = CoreJournal(self.pub)
            journal.begin_run("failed", trigger="scheduled").finish(
                phase="failed", source_state="not_requested", code="fetch_failed"
            )
            journal.begin_run("unknown-nochange", trigger="scheduled").finish(
                phase="no_change", source_state="not_requested"
            )
            journal.recover()  # Includes the actual unfinished pre-bind fixture run.
        finally:
            self.pub.release_run()
        runtime = MetadataRuntime(self.config)
        try:
            for tick in range(30):
                runtime.tick(now=100 + tick)
        except ValueError as error:
            self.fail(f"valid Core nullable outcome blocked progress: {error}")
        reader = RuntimeLedgerReader(self.config.ledger_path, "test", "fixed")
        rows = reader.read_runs_page()["items"]
        self.assertEqual(
            {"failed", "unfinished", "unknown-nochange"},
            {row["run_id"] for row in rows},
        )
        for row in rows:
            self.assertIsNone(row["original_observed"]["version"])
            self.assertEqual("ineligible_original_run", row["metadata_phase"])
        cursor = reader.read_cursor("outcomes")
        runtime = MetadataRuntime(self.config)
        prepared, desired = self.preparation(self.promote("baseline"))
        self.drive(runtime, prepared, desired)
        self.assertGreater(
            runtime.ledger.read_cursor("outcomes")["sequence"], cursor["sequence"]
        )
        self.assertEqual(
            0, runtime.store_reader.list_plans()["plans"][0]["expected_count"]
        )
        prepared, desired = self.preparation(self.promote("later", 124), (1,))
        self.drive(MetadataRuntime(self.config), prepared, desired)
        self.assertEqual(
            1, runtime.store_reader.list_plans()["plans"][-1]["expected_count"]
        )

    def test_large_retained_history_and_continuing_arrivals_do_not_expire_work(self):
        runtime = MetadataRuntime(self.config)
        # Observe actual ordinary Core outcomes into the actual Ledger, avoiding
        # the already-broken scheduler as fixture construction.
        for index in range(305):
            self.noop(f"old-{index}")
        from server_app.portraits.runtime_ledger import RuntimeLedger

        runtime.ledger = RuntimeLedger(
            self.config.ledger_path, "test", "fixed", enabled=True
        )
        with runtime.ledger.tick_owned():
            while runtime._discover(100):
                pass
        runtime = MetadataRuntime(self.config)  # Reconstruct authenticated frontier.
        prepared, desired = self.preparation(self.promote("new"), (1,))
        starts = []
        now = [101]

        def prepare(*args, **kwargs):
            starts.append(now[0])
            return prepared

        with (
            patch(
                "server_app.portraits.source_prepare.prepare_source",
                side_effect=prepare,
            ),
            patch(
                "server_app.portraits.source_prepare.read_prepared_source",
                return_value={"catalog": desired},
            ),
        ):
            for tick in range(360):
                now[0] = 101 + tick
                if tick % 3 == 0:
                    self.noop(f"arriving-{tick}")
                runtime.tick(now=now[0])
                if runtime.store_reader.list_plans()["plans"]:
                    break
        self.assertTrue(starts, "retained-history scans starved preparation")
        mapping = runtime.ledger.read_run_mapping(self.identity(runtime), "new")
        self.assertLess(starts[0], mapping["prepare_budget"]["deadline"])
        self.assertEqual(1, mapping["prepare_budget"]["attempts"])
        self.assertEqual("acked", mapping["metadata_phase"])

    def run_daemon(self, failures, error_type=PreparationError):
        from server_app.portraits import cli

        prepared, desired = self.preparation(self.promote("first"))
        runtime = PortraitRuntime(ExecutionConfig(metadata=self.config))
        clock = [100.0]
        calls = []
        ticks = []
        tick_runtime = runtime.tick

        def tick():
            result = tick_runtime()
            ticks.append(result)
            return result

        class Stop:
            def is_set(inner):
                return clock[0] >= 200

            def wait(inner, seconds):
                clock[0] += seconds

        runtime.stop_event = Stop()

        def prepare(*args, **kwargs):
            calls.append(clock[0])
            if len(calls) <= failures:
                raise error_type("controlled preparation failure")
            return prepared

        with (
            patch.object(cli, "load_config", return_value=runtime.config),
            patch.object(runtime, "tick", side_effect=tick),
            patch(
                "server_app.portraits.execution_runtime.PortraitRuntime",
                return_value=runtime,
            ),
            patch(
                "server_app.portraits.execution_runtime.time.time",
                side_effect=lambda: clock[0],
            ),
            patch(
                "server_app.portraits.source_prepare.prepare_source",
                side_effect=prepare,
            ),
            patch(
                "server_app.portraits.source_prepare.read_prepared_source",
                return_value={"catalog": desired},
            ),
            contextlib.redirect_stdout(io.StringIO()),
            contextlib.redirect_stderr(io.StringIO()),
        ):
            code = cli.main(["daemon", "--enable", "--config", "controlled"])
        if error_type is not PreparationError:
            self.assertEqual(2, code)
            self.assertEqual(1, len(calls))
            return
        self.assertEqual(0, code, "real daemon exited on a persisted expected retry")
        mapping = runtime.metadata.ledger.read_run_mapping(
            self.identity(runtime.metadata), "first"
        )
        self.assertEqual(
            min(failures + 1, 3), mapping["prepare_budget"]["attempts"], ticks[-15:]
        )
        self.assertEqual(
            "acked" if failures == 1 else "preparation_exhausted",
            mapping["metadata_phase"],
        )
        self.assertLessEqual(mapping["prepare_budget"]["deadline"], 410)
        prepared, desired = self.preparation(self.promote("later", 124))
        self.drive(MetadataRuntime(self.config), prepared, desired)
        later = runtime.metadata.ledger.read_run_mapping(
            self.identity(runtime.metadata), "later"
        )
        self.assertEqual("acked", later["metadata_phase"])

    def test_real_daemon_retries_once_then_succeeds(self):
        self.run_daemon(1)

    def test_real_daemon_exhausts_three_attempts_and_later_generation_progresses(self):
        self.run_daemon(3)

    def test_real_daemon_boundary_error_still_exits(self):
        from server_app.portraits.executor import BoundaryError

        self.run_daemon(1, BoundaryError)

    def test_real_daemon_cleanup_error_still_exits(self):
        from server_app.portraits.executor import CleanupError

        self.run_daemon(1, CleanupError)

    def test_restart_never_extends_an_already_observed_deadline(self):
        from server_app.portraits.runtime_ledger import RuntimeLedger

        self.promote("expired")
        runtime = MetadataRuntime(self.config)
        runtime.ledger = RuntimeLedger(
            self.config.ledger_path, "test", "fixed", enabled=True
        )
        with runtime.ledger.tick_owned():
            runtime._discover(100)
        original = runtime.ledger.read_run_mapping(self.identity(runtime), "expired")
        runtime = MetadataRuntime(self.config)
        for tick in range(12):
            runtime.tick(now=450 + tick)
        final = runtime.ledger.read_run_mapping(self.identity(runtime), "expired")
        self.assertEqual(
            original["source_budget"]["deadline"], final["source_budget"]["deadline"]
        )
        self.assertEqual(
            original["prepare_budget"]["deadline"], final["prepare_budget"]["deadline"]
        )
        self.assertEqual(0, final["prepare_budget"]["attempts"])
        self.assertIn(
            final["metadata_phase"], ("source_failed", "preparation_exhausted")
        )


if __name__ == "__main__":
    unittest.main()
