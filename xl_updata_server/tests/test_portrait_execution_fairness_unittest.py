"""Continuous ordinary observations cannot starve authenticated execution."""

import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import test_portrait_final_runtime_unittest as fixtures
from server_app.portraits.execution_runtime import ExecutionConfig, PortraitRuntime
from server_app.portraits.metadata_runtime import MetadataRuntime


class ExecutionFairnessTests(unittest.TestCase):
    setUp = fixtures.FinalRuntimeTests.setUp
    promote = fixtures.FinalRuntimeTests.promote
    preparation = fixtures.FinalRuntimeTests.preparation
    drive = fixtures.FinalRuntimeTests.drive
    noop = fixtures.FinalRuntimeTests.noop

    def test_continuing_noops_allow_registered_jobs_but_unknown_promotion_blocks(self):
        metadata = MetadataRuntime(self.config)
        prepared, desired = self.preparation(self.promote("baseline"))
        self.drive(metadata, prepared, desired)
        prepared, desired = self.preparation(self.promote("jobs", 124), (1,))
        self.drive(metadata, prepared, desired)
        runtime = PortraitRuntime(ExecutionConfig(metadata=self.config))
        with patch.object(
            runtime, "execute_tick", return_value={"status": "finished"}
        ) as execute:
            for tick in range(60):
                self.noop(f"stream-{tick}")
                runtime.tick()
                if execute.called:
                    break
            self.assertTrue(
                execute.called, "continuous no-op observations starved execution"
            )
            execute.reset_mock()
            self.promote("not-yet-observed", 125)
            runtime.tick()
            self.assertFalse(
                execute.called,
                "unobserved eligible promotion bypassed metadata barriers",
            )

    def test_continuing_noops_reach_actual_claim_finish_event_and_seal(self):
        from server_app.portraits.executor import StageResult
        from server_app.portraits.source_prepare import check_stage

        metadata = MetadataRuntime(self.config)
        prepared, desired = self.preparation(self.promote("baseline"))
        self.drive(metadata, prepared, desired)
        prepared, desired = self.preparation(self.promote("jobs", 124), (1,))
        self.drive(metadata, prepared, desired)
        runtime = PortraitRuntime(ExecutionConfig(metadata=self.config))
        execute = runtime.execute_tick
        removed = []

        def failed_render(slot, claim, ready, cancel):
            with tempfile.TemporaryDirectory() as directory:
                logs = Path(directory)
                removed.append(logs)
                (logs / "stderr.log").write_text("ValueError: private raw source")
                check_stage(StageResult(1, "exit"), stage_name="render", log_root=logs)

        with (
            patch.object(
                runtime,
                "execute_tick",
                side_effect=lambda ids: execute(ids, now=lambda: 200),
            ),
            patch.object(runtime, "_render", side_effect=failed_render) as render,
        ):
            for tick in range(60):
                self.noop(f"live-{tick}")
                runtime.tick()
                events = runtime.metadata.store_reader.read_events()["events"]
                if events:
                    break
        self.assertEqual(1, render.call_count)
        self.assertEqual(1, len(events))
        diagnostic = events[0]["result"]["diagnostics"][0]
        self.assertIn("render: stage failed", diagnostic)
        self.assertIn("ValueError", diagnostic)
        self.assertNotIn("private raw source", diagnostic)
        self.assertTrue(all(not path.exists() for path in removed))
        plan = runtime.metadata.store_reader.list_plans()["plans"][-1]
        self.assertTrue(plan["sealed"])
        self.assertEqual(
            1,
            runtime.metadata.store_reader.read_plan_snapshot(plan["plan_id"])["counts"][
                "failed"
            ],
        )


if __name__ == "__main__":
    unittest.main()
