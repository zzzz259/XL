import tempfile
import unittest
from pathlib import Path

from bot_app.config import load_config


class LegacyFeatureConfigTests(unittest.TestCase):
    def test_features_are_not_created_by_default(self):
        with tempfile.TemporaryDirectory() as directory:
            config_file = Path(directory) / "config.toml"
            config_file.write_text(
                '[bot]\nappid="app"\nsecret="secret"\n[watch]\noutbox_dir="/tmp/outbox"\n',
                encoding="utf-8",
            )
            config = load_config(str(config_file))
        self.assertFalse(hasattr(config.groups, "features"))

    def test_legacy_feature_levels_are_ignored(self):
        with tempfile.TemporaryDirectory() as directory:
            config_file = Path(directory) / "config.toml"
            config_file.write_text(
                '[bot]\nappid="app"\nsecret="secret"\n[watch]\noutbox_dir="/tmp/outbox"\n'
                '[features]\nrerun_schedule_query="test"\n',
                encoding="utf-8",
            )
            config = load_config(str(config_file))
        self.assertFalse(hasattr(config.groups, "features"))


if __name__ == "__main__":
    unittest.main()
