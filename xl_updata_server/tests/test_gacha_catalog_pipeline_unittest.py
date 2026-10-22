"""Publication integration that runs without game extraction/rendering dependencies."""

import copy
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from server_app.gacha_catalog import publish_catalog, stage_gacha_catalog
from server_app.pipeline import ProcessResult, UpdatePipeline

CATALOG = {
    "schema_version": 1,
    "version": "123",
    "cards": {"101": ["测试五星", 5]},
    "pools": {"201": "测试池"},
    "selectors": [1],
    "pool_meta": {
        "201": {
            "category": "limited",
            "pity_group": "limited",
            "featured_ids": [101],
            "featured_names": ["测试五星"],
            "hard_pity": 70,
        }
    },
}


class CatalogPipelineTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.addCleanup(self.temp.cleanup)

    def test_mirror_occurs_only_after_version_published(self):
        def processor(staging):
            publish_catalog(copy.deepcopy(CATALOG), staging / "gacha_config")
            self.assertFalse((self.root / "gacha_config" / "current.json").exists())
            return ProcessResult(
                version_timestamp=123, processed=True, schedule_enabled=False
            )

        result = UpdatePipeline(self.root, processor).run_once()
        self.assertTrue(result.processed)
        manifest = json.loads((self.root / "gacha_config" / "current.json").read_text())
        self.assertEqual(manifest["version"], "123")
        self.assertTrue(
            (
                self.root / "versions" / "123" / "gacha_config" / manifest["file"]
            ).is_file()
        )

    def test_failed_version_publish_does_not_change_public_config(self):
        public = self.root / "gacha_config"
        publish_catalog(copy.deepcopy(CATALOG), public)
        before = (public / "current.json").read_bytes()

        def processor(staging):
            c = copy.deepcopy(CATALOG)
            c["version"] = "124"
            publish_catalog(c, staging / "gacha_config")
            return ProcessResult(
                version_timestamp=124, processed=True, schedule_enabled=False
            )

        with patch(
            "server_app.pipeline.publish_version", side_effect=OSError("publish failed")
        ):
            result = UpdatePipeline(self.root, processor).run_once()
        self.assertIsNotNone(result.error)
        self.assertEqual((public / "current.json").read_bytes(), before)

    def test_missing_export_keeps_last_good_publication_and_character_update(self):
        public = self.root / "gacha_config"
        publish_catalog(copy.deepcopy(CATALOG), public)
        before = (public / "current.json").read_bytes()

        def processor(staging):
            lua = staging / "empty-lua"
            lua.mkdir()
            warnings = stage_gacha_catalog(lua, staging, "124")
            self.assertTrue(warnings)
            self.assertFalse((staging / "gacha_config").exists())
            character = staging / "character_data" / "current.json"
            character.parent.mkdir(exist_ok=True)
            character.write_text('{"version":124,"characters":{"202":{}}}')
            return ProcessResult(
                version_timestamp=124,
                processed=True,
                schedule_enabled=False,
                warnings=warnings,
            )

        result = UpdatePipeline(self.root, processor).run_once()
        self.assertTrue(result.processed)
        self.assertEqual((public / "current.json").read_bytes(), before)
        self.assertEqual(
            json.loads((self.root / "character_data" / "current.json").read_text())[
                "version"
            ],
            124,
        )

    def test_corrupt_staged_export_keeps_last_good_and_reports_warning(self):
        public = self.root / "gacha_config"
        publish_catalog(copy.deepcopy(CATALOG), public)
        before = (public / "current.json").read_bytes()

        def processor(staging):
            c = copy.deepcopy(CATALOG)
            c["version"] = "124"
            m = publish_catalog(c, staging / "gacha_config")
            (staging / "gacha_config" / m["file"]).write_text("{}")
            return ProcessResult(
                version_timestamp=124, processed=True, schedule_enabled=False
            )

        result = UpdatePipeline(self.root, processor).run_once()
        self.assertTrue(result.processed)
        self.assertTrue(any("gacha catalog" in warning for warning in result.warnings))
        self.assertEqual((public / "current.json").read_bytes(), before)
