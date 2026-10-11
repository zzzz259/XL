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


if __name__ == "__main__":
    unittest.main()
