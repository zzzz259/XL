import tempfile
import unittest
from pathlib import Path

from server_app.pipeline import ProcessResult, UpdatePipeline


class PipelineScheduleWarningTests(unittest.TestCase):
    def test_missing_enabled_schedule_mirror_is_returned_as_warning(self):
        with tempfile.TemporaryDirectory() as directory:
            data_dir = Path(directory)

            def processor(staging):
                (staging / "manifest.json").write_text('{"version": 123}', encoding="utf-8")
                return ProcessResult(
                    version_timestamp=123,
                    processed=True,
                    schedule_enabled=True,
                    warnings=("rerun_schedule: parser failed",),
                )

            with self.assertLogs("server_app.pipeline", level="ERROR"):
                result = UpdatePipeline(data_dir, processor=processor).run_once()

        self.assertTrue(result.processed)
        self.assertIsNone(result.error)
        self.assertEqual(len(result.warnings), 2)
        self.assertEqual(result.warnings[0], "rerun_schedule: parser failed")
        self.assertIn("rerun schedule", result.warnings[1].lower())

    def test_mirror_failure_warning_is_kept_with_five_existing_warnings(self):
        with tempfile.TemporaryDirectory() as directory:
            data_dir = Path(directory)

            def processor(staging):
                (staging / "manifest.json").write_text('{"version": 123}', encoding="utf-8")
                return ProcessResult(
                    version_timestamp=123,
                    processed=True,
                    schedule_enabled=True,
                    warnings=tuple(f"warning-{index}" for index in range(5)),
                )

            with self.assertLogs("server_app.pipeline", level="ERROR"):
                result = UpdatePipeline(data_dir, processor=processor).run_once()

        self.assertTrue(result.processed)
        self.assertEqual(len(result.warnings), 5)
        self.assertIsInstance(result.warnings, tuple)
        self.assertEqual(result.warnings[:4], ("warning-0", "warning-1", "warning-2", "warning-3"))
        self.assertIn("rerun schedule", result.warnings[-1].lower())


if __name__ == "__main__":
    unittest.main()
