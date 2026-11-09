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
        "basegacha.lua.bytes",
        "basegachabottomup.lua.bytes",
        "basegachabottompool.lua.bytes",
        "basefashion.lua.bytes",
    }
)


def select_lua_assets(index: CatalogIndex) -> tuple[AssetRef, ...]:
    return tuple(
        asset
        for asset in index.assets
        if asset.name.lower() in LUA_NAMES and asset.bundle_index >= 0
    )


def _prefab_stem(value: str) -> str:
    stem = str(value).replace("\\", "/").rsplit("/", 1)[-1].strip().casefold()
    return stem[: -len(".prefab")] if stem.endswith(".prefab") else stem


def select_portrait_prefab_assets(index: CatalogIndex, prefab_name: str) -> tuple[AssetRef, ...]:
    """按 BaseFashion 的 show_spine Prefab 在 Arts 清单中定位根资源。

    游戏配置存的是 Prefab 基名（如 ``cardspine_10051_3``），清单里的
    ``name`` 或 ``container`` 叶子可能带 ``.prefab`` 后缀；两种形态都匹配，
    但绝不用目录前缀或资源类型去猜。
    """
    wanted = _prefab_stem(prefab_name)
    if not wanted:
        return ()
    matches = []
    for asset in index.assets:
        if _prefab_stem(asset.name) == wanted:
            matches.append(asset)
            continue
        container = str(asset.container).replace("\\", "/").strip("/")
        if container and _prefab_stem(container) == wanted:
            matches.append(asset)
    return tuple(matches)


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
