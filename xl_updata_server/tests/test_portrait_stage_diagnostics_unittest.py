"""Bounded stage attribution remains available after owned attempt cleanup."""

import inspect
import tempfile
import unittest
from pathlib import Path

from server_app.portraits.executor import BoundaryError, Deferred, StageResult
from server_app.portraits.source_prepare import PreparationError, check_stage


class StageDiagnosticTests(unittest.TestCase):
    def test_timeout_has_fixed_stage_and_safe_bounded_stderr(self):
        self.assertIn("stage_name", inspect.signature(check_stage).parameters)
        with tempfile.TemporaryDirectory() as directory:
            logs = Path(directory)
            (logs / "stderr.log").write_text(
                "portrait render worker refused: ValueError: secret-key https://private/a\n"
                + "private-payload " * 10000
            )
            with self.assertRaises(PreparationError) as caught:
                check_stage(
                    StageResult(-1, "timeout"), stage_name="render", log_root=logs
                )
            diagnostic = str(caught.exception)
        self.assertIn("render", diagnostic)
        self.assertIn("timeout", diagnostic)
        self.assertIn("ValueError", diagnostic)
        self.assertNotIn("secret-key", diagnostic)
        self.assertNotIn("https://private", diagnostic)
        self.assertNotIn("private-payload", diagnostic)
        self.assertLessEqual(len(diagnostic), 512)

    def test_boundary_and_cooperative_outcomes_keep_exception_taxonomy(self):
        for result, kind in (
            (StageResult(3, "exit"), BoundaryError),
            (StageResult(2, "exit"), Deferred),
        ):
            with self.subTest(kind=kind), self.assertRaises(kind):
                check_stage(result)


if __name__ == "__main__":
    unittest.main()
