"""Local end-to-end test for decoded gacha Lua through rendered schedule output."""

import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

from server_app.processor import process_rerun_schedule
from server_app.rerun_schedule import read_current_schedule


class RerunScheduleProcessorTests(unittest.TestCase):
    def test_decoded_pool_tables_produce_versioned_json_and_png(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            lua = root / "lua"
            stage = root / "stage"
            lua.mkdir()
            (lua / "basegacha.lua").write_text(
                "BaseGacha = { [24000060] = { id=24000060, type=2, sort=60, bottom_up=1, "
                "bottom_main_type=1, main_card_ids={10000214} } }",
                encoding="utf-8",
            )
            (lua / "basegachabottomup.lua").write_text(
                "BaseGachaBottomUp = { [1] = { bottom_type=201 } }", encoding="utf-8"
            )
            config = SimpleNamespace(
                rerun_schedule_enabled=True,
                rerun_schedule_anchor=None,
                node_bin="node",
            )
            result = process_rerun_schedule(
                lua,
                stage,
                "134351472190013976",
                {"10000214": {"name": "朝雾"}},
                config,
            )

            self.assertEqual(result["queue"][0]["character_id"], 10000214)
            payload, image_path = read_current_schedule(stage / "rerun_schedule")
            self.assertEqual(payload["source_version"], "134351472190013976")
            self.assertEqual(payload["render"]["next"]["name"], "朝雾")
            self.assertTrue(image_path.is_file())
            self.assertTrue((stage / "rerun_schedule" / "current.png").is_file())

    def test_missing_gacha_lua_is_a_nonfatal_skip(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "lua").mkdir()
            result = process_rerun_schedule(
                root / "lua", root / "stage", "v1", {},
                SimpleNamespace(rerun_schedule_enabled=True),
            )
            self.assertIsNone(result)
            self.assertFalse((root / "stage" / "rerun_schedule").exists())


if __name__ == "__main__":
    unittest.main()
