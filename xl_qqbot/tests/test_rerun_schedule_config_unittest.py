import tempfile
import unittest
from pathlib import Path

from bot_app.config import load_config


class RerunScheduleFeatureConfigTests(unittest.TestCase):
    def test_query_defaults_to_debug_tier(self):
        with tempfile.TemporaryDirectory() as directory:
            config_file = Path(directory) / "config.toml"
            config_file.write_text(
                '[bot]\nappid="app"\nsecret="secret"\n[watch]\noutbox_dir="/tmp/outbox"\n',
                encoding="utf-8",
            )
            config = load_config(str(config_file))
        self.assertEqual(config.groups.features["rerun_schedule_query"], "debug")

    def test_explicit_feature_tier_can_be_promoted(self):
        with tempfile.TemporaryDirectory() as directory:
            config_file = Path(directory) / "config.toml"
            config_file.write_text(
                '[bot]\nappid="app"\nsecret="secret"\n[watch]\noutbox_dir="/tmp/outbox"\n'
                '[features]\nrerun_schedule_query="test"\n',
                encoding="utf-8",
            )
            config = load_config(str(config_file))
        self.assertEqual(config.groups.features["rerun_schedule_query"], "test")


if __name__ == "__main__":
    unittest.main()
