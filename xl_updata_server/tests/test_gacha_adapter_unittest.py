"""Standard-library regression tests for the safe gacha Lua reader."""

import unittest

from server_app.extractor import is_lua_asset_name
from server_app.gacha_adapter import load_gacha_tables, load_character_names, parse_lua_table
from server_app.catalog import AssetRef, BundleRef, CatalogIndex
from server_app.selector import LUA_NAMES, select_lua_assets


class GachaLuaAdapterTests(unittest.TestCase):
    def test_selector_and_extractor_include_gacha_tables(self):
        self.assertIn("basegacha.lua.bytes", LUA_NAMES)
        self.assertIn("basegachabottomup.lua.bytes", LUA_NAMES)
        self.assertTrue(is_lua_asset_name("BaseGacha.lua.bytes"))
        self.assertTrue(is_lua_asset_name("BaseGachaBottomUp.lua"))
        index = CatalogIndex(
            bundles=(BundleRef("lua.bundle", "lua", 1, ()),),
            assets=(
                AssetRef("BaseGacha.lua.bytes", 0, "Lua"),
                AssetRef("BaseGachaBottomUp.lua.bytes", 0, "Lua"),
            ),
        )
        self.assertEqual(len(select_lua_assets(index)), 2)

    def test_parses_nested_gacha_fields_and_translation_function_without_execution(self):
        source = '''
        BaseGacha = BaseGacha or {}
        BaseGacha[24000086] = {
          id = 24000086, type = 2, card_ids = {10000223, 10000224},
          main_card_ids = {10000223}, bottom_up = 9, bottom_main_type = 1,
          banner = "bracket } comma, safe", name = function() return T(8123) end,
        }
        '''
        parsed = parse_lua_table(source, "BaseGacha")
        self.assertEqual(parsed[24000086]["type"], 2)
        self.assertEqual(parsed[24000086]["card_ids"], [10000223, 10000224])
        self.assertEqual(parsed[24000086]["main_card_ids"], [10000223])
        self.assertEqual(parsed[24000086]["name"], {"__translation__": 8123})
        self.assertEqual(parsed[24000086]["banner"], "bracket } comma, safe")

    def test_parses_foreign_key_table_and_rejects_unclosed_table(self):
        parsed = parse_lua_table(
            "BaseGachaBottomUp = { [9] = { id=9, bottom_type=201 }, }",
            "BaseGachaBottomUp",
        )
        self.assertEqual(parsed[9]["bottom_type"], 201)
        with self.assertRaisesRegex(ValueError, "unterminated"):
            parse_lua_table("BaseGacha = { [1] = { type=2", "BaseGacha")

    def test_does_not_execute_source(self):
        source = "os.execute('touch SHOULD_NOT_EXIST'); BaseGacha = { [1] = { type=2 } }"
        self.assertEqual(parse_lua_table(source, "BaseGacha")[1]["type"], 2)

    def test_loads_gacha_tables_from_decoded_lua_directory(self):
        from tempfile import TemporaryDirectory
        from pathlib import Path

        with TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "basegacha.lua").write_text(
                "BaseGacha = { [24000086] = { type=2, bottom_up=9, bottom_main_type=1, main_card_ids={ [1]=10000223 } } }",
                encoding="utf-8",
            )
            (root / "basegachabottomup.lua").write_text(
                "BaseGachaBottomUp = { [9] = { bottom_type=201 } }", encoding="utf-8"
            )
            pools, bottomups = load_gacha_tables(root)

        self.assertEqual(pools[0]["id"], 24000086)
        self.assertEqual(pools[0]["main_card_ids"], [10000223])
        self.assertEqual(bottomups[9]["bottom_type"], 201)

    def test_resolves_headliner_names_through_basecard_translation_ids(self):
        from tempfile import TemporaryDirectory
        from pathlib import Path

        with TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "basecard.lua").write_text(
                "BaseCard = { [10000214] = { name=function() return T(90214) end } }",
                encoding="utf-8",
            )
            (root / "baseword_cn.lua").write_text(
                'BaseWord_cn = { [90214] = { name="朝雾" } }', encoding="utf-8"
            )
            names = load_character_names(root)
        self.assertEqual(names["10000214"], "朝雾")


if __name__ == "__main__":
    unittest.main()
