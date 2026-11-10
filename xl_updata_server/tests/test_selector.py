from server_app.catalog import AssetRef, BundleRef, CatalogIndex
from server_app.selector import (
    LUA_NAMES,
    bundle_hashes_for_assets,
    select_lua_assets,
    select_portrait_callback_lua_assets,
    select_portrait_prefab_assets,
)


def test_portrait_callback_scripts_are_selected_separately_from_card_lua():
    names = tuple(
        f"touch_cardspine_{name}_3.lua.bytes"
        for name in ("10016", "10098", "10102", "10103", "10111", "10119", "10120")
    )
    index = CatalogIndex(
        bundles=(BundleRef("callbacks.bundle", "callbacks", 100, ()),),
        assets=tuple(AssetRef(name, 0, "Assets/Lua") for name in names)
        + (AssetRef("touch_other.lua.bytes", 0, "Assets/Lua"),),
    )
    assert tuple(asset.name for asset in select_portrait_callback_lua_assets(index)) == names
    assert select_lua_assets(index) == ()


def test_lua_whitelist_has_gacha_tables_and_no_handbook():
    assert "basecardhandbook.lua.bytes" not in LUA_NAMES
    assert "basecard.lua.bytes" in LUA_NAMES
    assert "baseword_cn.lua.bytes" in LUA_NAMES
    assert "basegacha.lua.bytes" in LUA_NAMES
    assert "basegachabottomup.lua.bytes" in LUA_NAMES
    assert "basegachabottompool.lua.bytes" in LUA_NAMES
    assert "basefashion.lua.bytes" in LUA_NAMES


def test_selector_recognizes_character_lua_names():
    index = CatalogIndex(
        bundles=(BundleRef("assets_luaconfig.bundle", "lua", 100, ()),),
        assets=(
            AssetRef("BaseCard.lua.bytes", 0, "Assets/Lua"),
            AssetRef("BaseWord_cn.lua.bytes", 0, "Assets/Lua"),
            AssetRef("BaseGachaBottomPool.lua.bytes", 0, "Assets/Lua"),
            AssetRef("unrelated.lua.bytes", 0, "Assets/Lua"),
        ),
    )
    assert [asset.name for asset in select_lua_assets(index)] == ["BaseCard.lua.bytes", "BaseWord_cn.lua.bytes", "BaseGachaBottomPool.lua.bytes"]
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


def test_portrait_prefab_assets_match_name_or_container_leaf():
    index = CatalogIndex(
        bundles=(
            BundleRef("root.bundle", "root", 100, (1,)),
            BundleRef("shared.bundle", "shared", 50, ()),
        ),
        assets=(
            AssetRef("cardspine_10051_3.prefab", 0, "Assets/Arts/CardSpine"),
            AssetRef("body.png", 1, "Assets/Arts/UI"),
            AssetRef("cardspine_10060_1.prefab", 1, "Assets/Arts/CardSpine"),
        ),
    )

    matches = select_portrait_prefab_assets(index, "cardspine_10051_3")
    assert [asset.name for asset in matches] == ["cardspine_10051_3.prefab"]
    # .prefab 后缀与大小写不敏感
    assert select_portrait_prefab_assets(index, "CardSpine_10051_3.prefab")
    # 目录名不相同的 prefab 不会被误伤
    assert [asset.name for asset in select_portrait_prefab_assets(index, "cardspine_10060_1")] == [
        "cardspine_10060_1.prefab"
    ]
    assert select_portrait_prefab_assets(index, "absent") == ()


def test_portrait_prefab_bundle_closure_includes_dependencies():
    index = CatalogIndex(
        bundles=(
            BundleRef("root.bundle", "root", 100, (1,)),
            BundleRef("shared.bundle", "shared", 50, (2,)),
            BundleRef("base.bundle", "base", 10, ()),
        ),
        assets=(AssetRef("cardspine_10051_3.prefab", 0, "Assets/Arts/CardSpine"),),
    )

    matches = select_portrait_prefab_assets(index, "cardspine_10051_3")
    assert bundle_hashes_for_assets(index, matches) == ("base", "root", "shared")
