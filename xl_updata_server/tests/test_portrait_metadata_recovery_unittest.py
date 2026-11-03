"""Real authority regressions for review R1-R3; no Native execution."""

import unittest
from unittest.mock import patch

import test_portrait_metadata_runtime_unittest as fixtures
from server_app.portraits.core_outcomes import CoreJournal
from server_app.portraits.metadata_runtime import MetadataRuntime
from server_app.portraits.runtime_ledger import RuntimeLedger


class RecoveryTests(unittest.TestCase):
    setUp = fixtures.FlowTests.setUp
    promote = fixtures.FlowTests.promote
    preparation = fixtures.FlowTests.preparation
    drive = fixtures.FlowTests.drive

    def mapping(self, runtime, run="a"):
        return next(
            m for m in runtime.ledger.read_runs_page()["items"] if m["run_id"] == run
        )

    def final_source_crash(self, *, failed=False):
        runtime = MetadataRuntime(self.config)
        pin = self.promote()
        for _ in range(10):
            runtime.tick(now=100)
            if runtime.ledger.read_runs_page()["items"]:
                break
        identity = self.mapping(runtime)["core_identity"]
        with runtime.ledger.tick_owned():
            self.assertTrue(runtime.ledger.start_source(identity, "a", now=100))
            runtime.ledger.retry_source(identity, "a", now=100)
            self.assertTrue(runtime.ledger.start_source(identity, "a", now=102))
            runtime.ledger.retry_source(identity, "a", now=102)
        original = CoreJournal.resolve_pending_source

        def resolve(journal, *args, **kwargs):
            if failed and not kwargs.get("existing_only", False):
                journal.fail_source("a", code="source_invalid")
            return original(journal, *args, **kwargs)

        boundary = "source_failed" if failed else "source_ready"
        with (
            patch.object(CoreJournal, "resolve_pending_source", resolve),
            patch.object(
                RuntimeLedger,
                boundary,
                side_effect=SystemExit("native committed before ledger"),
            ),
            self.assertRaises(SystemExit),
        ):
            for _ in range(15):
                runtime.tick(now=110)
        self.assertEqual(3, self.mapping(runtime)["source_budget"]["attempts"])
        return runtime, pin

    def ready_recovery(self, now):
        original, pin = self.final_source_crash()
        budget = self.mapping(original)["source_budget"]
        runtime = MetadataRuntime(self.config)
        prepared, desired = self.preparation(pin)
        with (
            patch.object(
                CoreJournal,
                "fail_source",
                side_effect=AssertionError("must preserve ready"),
            ),
            patch(
                "server_app.portraits.source_prepare.prepare_source",
                return_value=prepared,
            ),
            patch(
                "server_app.portraits.source_prepare.read_prepared_source",
                return_value={"catalog": desired},
            ),
        ):
            for _ in range(80):
                runtime.tick(now=now)
        mapping = self.mapping(runtime)
        self.assertEqual(budget, mapping["source_budget"])
        self.assertEqual("ready", runtime.core.read_source_links()[0]["state"])
        self.assertEqual(
            "acked" if now < 400 else "preparation_exhausted", mapping["metadata_phase"]
        )

    def test_r1_ready_after_final_attempt_before_deadline(self):
        self.ready_recovery(120)

    def test_r1_ready_after_final_attempt_after_deadline(self):
        self.ready_recovery(500)

    def test_r1_existing_failed_exact_code_after_crash(self):
        original, _ = self.final_source_crash(failed=True)
        budget = self.mapping(original)["source_budget"]
        runtime = MetadataRuntime(self.config)
        with patch.object(
            CoreJournal,
            "fail_source",
            side_effect=AssertionError("do not overwrite native failure"),
        ):
            for _ in range(50):
                runtime.tick(now=500)
        self.assertEqual("source_invalid", self.mapping(runtime)["failure_code"])
        self.assertEqual(budget, self.mapping(runtime)["source_budget"])

    def failure_completion(self, transient):
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
            for step in range(40):
                runtime.tick(now=100 + step)
                rows = runtime.ledger.read_runs_page()["items"]
                if (
                    rows
                    and self.mapping(runtime)["metadata_phase"]
                    == "source_resolution_exhausted"
                ):
                    break
        if transient:
            with patch.object(
                CoreJournal,
                "fail_source",
                side_effect=OSError("temporary link storage"),
            ) as failure:
                for _ in range(10):
                    runtime.tick(now=150)
                    if failure.call_count:
                        break
            self.assertEqual(
                "fail_link_pending", self.mapping(runtime)["metadata_phase"]
            )
        original = CoreJournal.fail_source
        calls = []

        def record(journal, *args, **kwargs):
            calls.append(args[0])
            return original(journal, *args, **kwargs)

        runtime = MetadataRuntime(self.config)
        newer = self.preparation(self.promote("b", 124))
        with patch.object(CoreJournal, "fail_source", record):
            self.drive(runtime, *newer, count=120)
            runtime = MetadataRuntime(self.config)
            self.drive(runtime, *newer, count=30)
        self.assertEqual(["a"], calls)
        self.assertEqual("source_failed", self.mapping(runtime)["metadata_phase"])
        self.assertEqual(
            "source_resolution_exhausted", self.mapping(runtime)["failure_code"]
        )
        self.assertEqual("acked", self.mapping(runtime, "b")["metadata_phase"])

    def test_r2_exhausted_old_run_finishes_failure_and_does_not_block_new_core(self):
        self.failure_completion(False)

    def test_r2_pending_failure_link_restart_then_success_does_not_block_new_core(self):
        self.failure_completion(True)

    def alias_crash(self, after, supersede):
        from server_app.portraits import publisher

        runtime = MetadataRuntime(self.config)
        first = self.preparation(self.promote("a", 123))
        self.drive(runtime, *first)
        self.drive(runtime, *self.preparation(self.promote("c", 124), (1,)))
        self.promote("a2", 123)
        publish = publisher._LockedMutation.publish_desired

        def crash(mutation, catalog, **kwargs):
            if after:
                publish(mutation, catalog, **kwargs)
            raise SystemExit("alias publication boundary")

        with (
            patch.object(publisher._LockedMutation, "publish_desired", crash),
            self.assertRaises(SystemExit),
        ):
            self.drive(runtime, *first, count=120)
        pending = self.mapping(runtime, "a2")
        self.assertEqual("alias_registered", pending["metadata_phase"])
        self.assertEqual("alias_pending", pending["disposition"])
        self.assertEqual(
            self.mapping(runtime)["delivery_owner"], pending["delivery_owner"]
        )
        newer = self.preparation(self.promote("d", 125), (1,)) if supersede else first
        runtime = MetadataRuntime(self.config)
        versions = []

        def record(mutation, catalog, **kwargs):
            versions.append(catalog["version"])
            return publish(mutation, catalog, **kwargs)

        with patch.object(publisher._LockedMutation, "publish_desired", record):
            self.drive(runtime, *newer, count=160)
        alias = self.mapping(runtime, "a2")
        self.assertEqual("existing_plan_alias", alias["metadata_phase"])
        self.assertEqual(self.mapping(runtime)["plan_id"], alias["plan_id"])
        if supersede:
            self.assertNotIn("123", versions)
            frozen = runtime.ledger.read_intent(
                self.mapping(runtime, "d")["core_identity"], "d"
            )
            self.assertEqual(
                "a2", frozen["comparison_evidence"]["effective_core"]["run_id"]
            )
            self.assertEqual(1, len(frozen["jobs"]))
        else:
            self.assertIn("123", versions)
            self.assertEqual(
                "123", publisher.read_catalog(self.config.portrait_root)["version"]
            )

    def test_r3_alias_after_publish_crash_then_new_core(self):
        self.alias_crash(True, True)

    def test_r3_alias_before_publish_crash_then_new_core(self):
        self.alias_crash(False, True)

    def test_r3_alias_before_publish_crash_still_current_replays_publication(self):
        self.alias_crash(False, False)

    def test_r1_existing_only_never_mints_from_absent_request_or_bare_proof(self):
        import inspect

        self.assertIn(
            "existing_only",
            inspect.signature(CoreJournal.resolve_pending_source).parameters,
        )
        self.promote()
        self.assertTrue(self.pub.acquire_run())
        try:
            journal = CoreJournal(self.pub)
            from server_app.portraits.core_outcomes import CoreReader

            cursor = CoreReader(
                journal.path, scope=self.pub.scope
            ).read_outcomes_page()["cursor"]
            for invalid in (None, 1, "true"):
                with self.assertRaises(ValueError):
                    journal.resolve_pending_source(
                        "a", outcomes_cursor=cursor, existing_only=invalid
                    )
            with (
                patch.object(
                    self.pub, "complete", side_effect=AssertionError("fresh complete")
                ),
                patch.object(
                    self.pub, "_prove", side_effect=AssertionError("fresh attest")
                ),
            ):
                self.assertEqual(
                    "missing_existing_resolution",
                    journal.resolve_pending_source(
                        "a", outcomes_cursor=cursor, existing_only=True
                    )["code"],
                )
            self.pub.attest_published(123, schedule_enabled=False)
            with (
                patch.object(
                    self.pub, "complete", side_effect=AssertionError("fresh complete")
                ),
                patch.object(
                    self.pub, "_prove", side_effect=AssertionError("fresh attest")
                ),
            ):
                self.assertEqual(
                    "missing_existing_resolution",
                    journal.resolve_pending_source(
                        "a", outcomes_cursor=cursor, existing_only=True
                    )["code"],
                )
        finally:
            self.pub.release_run()

    def test_r1_existing_only_supplied_ref_does_not_bypass_missing_request(self):
        self.promote(unknown=True)
        self.assertTrue(self.pub.acquire_run())
        try:
            from server_app.portraits.core_outcomes import CoreReader

            journal = CoreJournal(self.pub)
            cursor = CoreReader(
                journal.path, scope=self.pub.scope
            ).read_outcomes_page()["cursor"]
            ref = self.pub.list_requests(limit=1)[0].ref
            (self.pub.root / self.pub.scope / "requests" / "123.json").unlink()
            with patch.object(
                self.pub, "complete", side_effect=AssertionError("fresh request")
            ):
                result = journal.resolve_pending_source(
                    "a", outcomes_cursor=cursor, ready_ref=ref, existing_only=True
                )
            self.assertEqual("missing_existing_resolution", result["code"])
        finally:
            self.pub.release_run()
