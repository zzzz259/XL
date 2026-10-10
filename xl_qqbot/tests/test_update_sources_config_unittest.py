import tempfile
import unittest
from pathlib import Path

from bot_app.config import load_config


BASE = '''
[bot]
appid = "app"
secret = "secret"

[watch]
outbox_dir = "/srv/main/data/outbox"
'''


class UpdateSourceConfigTests(unittest.TestCase):
    def _load(self, content):
        tmp = tempfile.NamedTemporaryFile(mode="w", suffix=".toml", delete=False)
        try:
            tmp.write(content)
            tmp.close()
            return load_config(tmp.name)
        finally:
            Path(tmp.name).unlink(missing_ok=True)

    def test_legacy_config_defaults_to_main_source(self):
        config = self._load(BASE)

        self.assertEqual(len(config.watch.update_sources), 1)
        self.assertEqual(config.watch.update_sources[0].name, "main")
        self.assertEqual(config.watch.update_sources[0].outbox_dir, "/srv/main/data/outbox")
        self.assertEqual(config.watch.update_sources[0].minimum_tier, "production")

    def test_explicit_test_source_has_minimum_test_tier(self):
        config = self._load(
            BASE
            + '''
[[watch.update_sources]]
name = "main"
outbox_dir = "/srv/main/data/outbox"
minimum_tier = "production"

[[watch.update_sources]]
name = "test"
outbox_dir = "/srv/test/data/outbox"
minimum_tier = "test"
'''
        )

        self.assertEqual(
            [(item.name, item.outbox_dir, item.minimum_tier) for item in config.watch.update_sources],
            [
                ("main", "/srv/main/data/outbox", "production"),
                ("test", "/srv/test/data/outbox", "test"),
            ],
        )

    def test_explicit_sources_must_include_main(self):
        with self.assertRaisesRegex(ValueError, "main"):
            self._load(
                BASE
                + '''
[[watch.update_sources]]
name = "test"
outbox_dir = "/srv/test/data/outbox"
minimum_tier = "test"
'''
            )

    def test_source_name_and_minimum_tier_are_validated(self):
        with self.assertRaisesRegex(ValueError, "minimum_tier"):
            self._load(
                BASE
                + '''
[[watch.update_sources]]
name = "main"
outbox_dir = "/srv/main/data/outbox"
minimum_tier = "everyone"
'''
            )

    def test_source_names_must_be_unique(self):
        with self.assertRaisesRegex(ValueError, "重复"):
            self._load(
                BASE
                + '''
[[watch.update_sources]]
name = "main"
outbox_dir = "/srv/main/data/outbox"

[[watch.update_sources]]
name = "main"
outbox_dir = "/srv/main/data/other-outbox"
'''
            )


if __name__ == "__main__":
    unittest.main()
