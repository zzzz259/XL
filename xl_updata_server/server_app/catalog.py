"""UnityFS 分类清单读取。"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import UnityPy


@dataclass(frozen=True)
class BundleRef:
    name: str
    hash: str
    size: int
    deps: tuple[int, ...]


@dataclass(frozen=True)
class AssetRef:
    name: str
    bundle_index: int
    container: str


@dataclass(frozen=True)
class CatalogIndex:
    bundles: tuple[BundleRef, ...]
    assets: tuple[AssetRef, ...]


def read_catalog(path: str | Path, decrypt_key: bytes) -> CatalogIndex:
    UnityPy.set_assetbundle_decrypt_key(decrypt_key)
    environment = UnityPy.load(str(path))
    catalog = None
    for obj in environment.objects:
        if getattr(getattr(obj, "type", None), "name", "") != "AssetBundle":
            continue
        bundle = obj.read()
        for _container_name, asset_info in getattr(bundle, "m_Container", ()):
            pointer = getattr(asset_info, "asset", None)
            candidate = pointer.read() if pointer is not None else None
            if hasattr(candidate, "bundles") and hasattr(candidate, "assets"):
                catalog = candidate
                break
        if catalog is not None:
            break
    if catalog is None:
        raise ValueError(f"未找到分类清单对象: {path}")

    dirs = tuple(str(value) for value in getattr(catalog, "dirs", ()))
    bundles = tuple(
        BundleRef(
            name=str(getattr(item, "name", "")),
            hash=str(getattr(item, "hash", "")),
            size=int(getattr(item, "size", 0) or 0),
            deps=tuple(int(dep) for dep in getattr(item, "deps", ()) if int(dep) >= 0),
        )
        for item in getattr(catalog, "bundles", ())
    )
    assets = []
    for item in getattr(catalog, "assets", ()):
        directory_index = int(getattr(item, "dir", -1))
        container = dirs[directory_index] if 0 <= directory_index < len(dirs) else ""
        assets.append(
            AssetRef(
                name=str(getattr(item, "name", "")),
                bundle_index=int(getattr(item, "bundle", -1)),
                container=container,
            )
        )
    return CatalogIndex(bundles=bundles, assets=tuple(assets))
