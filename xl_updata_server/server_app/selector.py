"""从分类清单中选择角色 Lua 资源。"""

from __future__ import annotations

from .catalog import AssetRef, CatalogIndex

LUA_NAMES = frozenset(
    {
        "basecard.lua.bytes",
        "basecardlevelup.lua.bytes",
        "basecardqualityup.lua.bytes",
        "baseitem.lua.bytes",
        "basebadgesuitgroup.lua.bytes",
        "basecvnamecn.lua.bytes",
        "baseskill.lua.bytes",
        "baseskilllevelup.lua.bytes",
        "baseword_cn.lua.bytes",
    }
)


def select_lua_assets(index: CatalogIndex) -> tuple[AssetRef, ...]:
    return tuple(
        asset
        for asset in index.assets
        if asset.name.lower() in LUA_NAMES and asset.bundle_index >= 0
    )


def bundle_hashes_for_assets(index: CatalogIndex, assets: tuple[AssetRef, ...]) -> tuple[str, ...]:
    selected = {
        asset.bundle_index
        for asset in assets
        if 0 <= asset.bundle_index < len(index.bundles)
    }
    for _ in range(len(index.bundles)):
        selected.update(
            dependency
            for bundle_index in tuple(selected)
            for dependency in index.bundles[bundle_index].deps
            if 0 <= dependency < len(index.bundles)
        )
    return tuple(sorted(index.bundles[bundle_index].hash for bundle_index in selected if index.bundles[bundle_index].hash))
