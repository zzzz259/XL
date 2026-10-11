"""Local end-to-end test for decoded gacha Lua through rendered schedule output."""

import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from server_app.processor import _attempt_rerun_schedule, process_rerun_schedule
from server_app.rerun_schedule import read_current_schedule

PNG_1PX = bytes.fromhex(
    "89504e470d0a1a0a0000000d49484452000000010000000108060000001f15c489"
    "0000000b49444154789c636000020000050001a5f645400000000049454e44ae426082"
)

class RerunScheduleProcessorTests(unittest.TestCase):
    def test_schedule_parse_failure_is_returned_as_nonfatal_warning(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            with patch(
                "server_app.processor.process_rerun_schedule",
                side_effect=ValueError("malformed Lua table key"),
            ):
                with self.assertLogs("server_app.processor", level="ERROR"):
                    result, warnings = _attempt_rerun_schedule(
                        root / "lua", root / "stage", "v2", {}, SimpleNamespace()
                    )

        self.assertIsNone(result)
        self.assertEqual(warnings, ("rerun_schedule: malformed Lua table key",))

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
            with patch(
                "server_app.processor.render_schedule_png", return_value=PNG_1PX
            ):
                result = process_rerun_schedule(
                    lua,
                    stage,
                    "134351472190013976",
                    {"10000214": {"name": "朝雾"}},
                    config,
                )

            self.assertIsNotNone(result)
            self.assertEqual(result["queue"][0]["character_id"], 10000214)
            payload, image_path = read_current_schedule(stage / "rerun_schedule")
            self.assertEqual(payload["source_version"], "134351472190013976")
            self.assertEqual(payload["render"]["next"]["name"], "朝雾")
            self.assertTrue(image_path.is_file())
            self.assertTrue((stage / "rerun_schedule" / "current.png").is_file())

    def test_mixed_case_lua_names_are_resolved_on_case_sensitive_filesystems(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            lua = root / "lua"
            stage = root / "stage"
            lua.mkdir()
            (lua / "BaseGacha.lua").write_text(
                "BaseGacha = { [24000060] = { id=24000060, type=2, sort=60, bottom_up=1, "
                "bottom_main_type=1, main_card_ids={10000214} } }",
                encoding="utf-8",
            )
            (lua / "BaseGachaBottomUp.lua").write_text(
                "BaseGachaBottomUp = { [1] = { bottom_type=201 } }", encoding="utf-8"
            )
            config = SimpleNamespace(
                rerun_schedule_enabled=True,
                rerun_schedule_anchor=None,
                node_bin="node",
            )
            actual_is_file = Path.is_file

            def linux_case_sensitive_is_file(path):
                if path.parent == lua and path.name.casefold() in {
                    "basegacha.lua",
                    "basegachabottomup.lua",
                }:
                    return path.name in {item.name for item in lua.iterdir()}
                return actual_is_file(path)

            with (
                patch.object(Path, "is_file", linux_case_sensitive_is_file),
                patch("server_app.processor.render_schedule_png", return_value=PNG_1PX),
            ):
                result = process_rerun_schedule(
                    lua,
                    stage,
                    "134351472190013976",
                    {"10000214": {"name": "朝雾"}},
                    config,
                )

            self.assertIsNotNone(result)
            payload, image_path = read_current_schedule(stage / "rerun_schedule")
            self.assertEqual(payload["render"]["next"]["name"], "朝雾")
            self.assertTrue(image_path.is_file())

    def test_computed_word_lookup_does_not_block_schedule_render(self):
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
            (lua / "basecard.lua").write_text(
                "BaseCard = { [10000214] = { name=function() return T(90214) end } }",
                encoding="utf-8",
            )
            (lua / "baseword_cn.lua").write_text(
                'BaseWord_cn = { [90214] = { name="朝雾" } }\n'
                'function lookup(locale, word_id) return BaseWord_cn[locale .. word_id] end',
                encoding="utf-8",
            )
            config = SimpleNamespace(
                rerun_schedule_enabled=True,
                rerun_schedule_anchor=None,
                rerun_schedule_forecast_limit=20,
                node_bin="node",
            )

            with patch(
                "server_app.processor.render_schedule_png", return_value=PNG_1PX
            ):
                result = process_rerun_schedule(lua, stage, "v2", {}, config)
            self.assertTrue((stage / "rerun_schedule" / "current.png").is_file())

        self.assertEqual(result["render"]["next"]["name"], "朝雾")

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
