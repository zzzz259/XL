from server_app.catalog import AssetRef, BundleRef, CatalogIndex
from server_app.selector import LUA_NAMES, bundle_hashes_for_assets, select_lua_assets


def test_lua_whitelist_has_nine_entries_and_no_handbook():
    assert len(LUA_NAMES) == 9
    assert "basecardhandbook.lua.bytes" not in LUA_NAMES
    assert "basecard.lua.bytes" in LUA_NAMES
    assert "baseword_cn.lua.bytes" in LUA_NAMES


def test_selector_recognizes_character_lua_names():
    index = CatalogIndex(
        bundles=(BundleRef("assets_luaconfig.bundle", "lua", 100, ()),),
        assets=(
            AssetRef("BaseCard.lua.bytes", 0, "Assets/Lua"),
            AssetRef("BaseWord_cn.lua.bytes", 0, "Assets/Lua"),
            AssetRef("unrelated.lua.bytes", 0, "Assets/Lua"),
        ),
    )
    assert [asset.name for asset in select_lua_assets(index)] == ["BaseCard.lua.bytes", "BaseWord_cn.lua.bytes"]
    assert bundle_hashes_for_assets(index, select_lua_assets(index)) == ("lua",)


def test_selector_includes_material_badge_and_quality_lua_names():
    index = CatalogIndex(
        bundles=(BundleRef("assets_luaconfig.bundle", "lua", 100, ()),),
        assets=(
            AssetRef("BaseItem.lua.bytes", 0, "Assets/Lua"),
            AssetRef("BaseBadgeSuitGroup.lua.bytes", 0, "Assets/Lua"),
            AssetRef("BaseCardQualityUp.lua.bytes", 0, "Assets/Lua"),
        ),
    )

    assert [asset.name for asset in select_lua_assets(index)] == [
        "BaseItem.lua.bytes",
        "BaseBadgeSuitGroup.lua.bytes",
        "BaseCardQualityUp.lua.bytes",
    ]
