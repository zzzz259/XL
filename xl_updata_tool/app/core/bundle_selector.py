"""按资源映射筛选 AssetBundle，避免单分类导出扫描整个版本目录。"""

from __future__ import annotations

import json
import os
from pathlib import Path


LUA_CONTAINER_PREFIX = "assets/lua"


def _normalise_source(source: str) -> str:
    return os.path.normcase(os.path.normpath(str(source))).replace("/", "\\")


def _is_lua_container(container: str) -> bool:
    value = str(container or "").replace("\\", "/").strip("/").lower()
    return value == LUA_CONTAINER_PREFIX or value.startswith(f"{LUA_CONTAINER_PREFIX}/")


def _load_assets_map(map_path: str | os.PathLike[str]) -> list[dict] | None:
    path = Path(map_path)
    if not path.is_file():
        return None
    try:
        with path.open(encoding="utf-8") as handle:
            payload = json.load(handle)
    except (OSError, ValueError, TypeError):
        return None
    return payload if isinstance(payload, list) else None


def select_lua_bundles(
    bundle_paths: list[str],
    map_path: str | os.PathLike[str],
) -> tuple[list[str], bool, int]:
    """从 ``assets_map.json`` 筛选包含 Lua 资源的 bundle。

    返回 ``(paths, mapped, asset_count)``：

    - ``mapped=False`` 表示映射不可用，调用方应采用兼容回退，不要误报“已精准筛选”；
    - 映射存在但没有 Lua 资源时返回空列表，表示该版本确实没有可导出的 Lua；
    - 结果保持输入顺序，避免影响已有进度和日志。
    """
    assets = _load_assets_map(map_path)
    if assets is None:
        return list(bundle_paths), False, 0

    source_names: set[str] = set()
    asset_count = 0
    for asset in assets:
        if not isinstance(asset, dict) or not _is_lua_container(asset.get("Container", "")):
            continue
        source = str(asset.get("Source", "") or "")
        if not source:
            continue
        source_names.add(_normalise_source(Path(source).name))
        asset_count += 1

    selected = [
        path for path in bundle_paths
        if _normalise_source(Path(path).name) in source_names
    ]
    return selected, True, asset_count


def lua_assets_map_path(bundle_dir: str | os.PathLike[str]) -> str:
    """返回某个版本的标准资源映射路径。"""
    return os.path.join(os.fspath(bundle_dir), "_map", "assets_map.json")
