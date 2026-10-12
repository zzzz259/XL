"""Version-bound index for AssetStudio catalog entries."""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any


@dataclass(frozen=True, slots=True)
class IndexedAsset:
    name: str
    container: str
    bundle_hash: str
    path_id: int
    dependencies: tuple[Any, ...]
    version_timestamp: int
    bundle_path: Path
    bundle_present: bool
    evidence: str


@dataclass(frozen=True, slots=True)
class AssetIndex:
    version_timestamp: int
    assets: tuple[IndexedAsset, ...]

    @classmethod
    def from_catalog(cls, catalog_path: Path, bundle_root: Path,
                     version_timestamp: int) -> "AssetIndex":
        catalog_path, bundle_root = Path(catalog_path), Path(bundle_root)
        payload: dict[str, Any] = json.loads(catalog_path.read_text(encoding="utf-8-sig"))
        catalog_version = payload.get("version_timestamp")
        if catalog_version is not None and int(catalog_version) != int(version_timestamp):
            raise ValueError(f"catalog version {catalog_version} does not match requested {version_timestamp}")
        records = []
        for number, raw in enumerate(payload.get("assets", ()), start=1):
            bundle_hash = str(raw.get("bundle_hash", ""))
            path = bundle_root / bundle_hash
            records.append(IndexedAsset(
                name=str(raw.get("name", "")),
                container=str(raw.get("container", "")).replace("\\", "/").strip("/"),
                bundle_hash=bundle_hash,
                path_id=int(raw["path_id"]),
                dependencies=tuple(raw.get("dependencies", ())),
                version_timestamp=int(version_timestamp), bundle_path=path,
                bundle_present=bool(bundle_hash) and path.is_file(),
                evidence=f"{catalog_path}#/assets/{number - 1}",
            ))
        return cls(int(version_timestamp), tuple(records))

    def find_exact(self, bundle_hash: str, path_id: int) -> tuple[IndexedAsset, ...]:
        """Return exact version-bound matches without guessing by asset name."""
        return tuple(asset for asset in self.assets
                     if asset.bundle_hash.casefold() == str(bundle_hash).casefold()
                     and asset.path_id == int(path_id)
                     and asset.version_timestamp == self.version_timestamp)

    def has_bundle(self, bundle_hash: str) -> bool:
        return any(asset.bundle_hash.casefold() == str(bundle_hash).casefold()
                   and asset.version_timestamp == self.version_timestamp for asset in self.assets)

    def find_external(self, identifier: str, path_id: int) -> tuple[IndexedAsset, ...]:
        """Resolve a Unity CAB identifier to exact bundle hash/path ID matches."""
        bundle_hash = canonical_bundle_hash(identifier)
        if bundle_hash is None:
            return ()
        return self.find_exact(bundle_hash, path_id)

    def resolve(self, container: str, name: str) -> IndexedAsset | None:
        normalized = str(container).replace("\\", "/").strip("/").casefold()
        wanted = str(name).replace("\\", "/").rsplit("/", 1)[-1].casefold()
        matches = [asset for asset in self.assets
                   if asset.container.casefold() == normalized
                   and asset.name.replace("\\", "/").rsplit("/", 1)[-1].casefold() == wanted]
        if len(matches) > 1:
            raise ValueError(f"ambiguous catalog asset: {container}/{name}")
        return matches[0] if matches else None


def canonical_bundle_hash(identifier: str) -> str | None:
    """Extract only a concrete 32-hex Unity CAB/bundle identifier."""
    value = str(identifier).strip().replace("\\", "/")
    match = re.fullmatch(
        r"(?i)(?:archive:/)?(?:CAB-)?([0-9a-f]{32})(?:/CAB-[0-9a-f]{32})?/?",
        value,
    )
    if not match:
        return None
    first = match.group(1).lower()
    # If both archive path components carry a hash, they must agree exactly.
    tail = re.search(r"(?i)/CAB-([0-9a-f]{32})/?$", value)
    if tail and tail.group(1).lower() != first:
        return None
    return first
