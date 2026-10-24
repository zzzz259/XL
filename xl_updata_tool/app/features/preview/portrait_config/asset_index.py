"""Version-bound index for AssetStudio catalog entries."""

from __future__ import annotations

import json
import ntpath
import re
from dataclasses import dataclass
from pathlib import Path
from pathlib import PurePosixPath
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
    asset_type: str = ""


@dataclass(frozen=True, slots=True)
class AssetIndex:
    version_timestamp: int
    assets: tuple[IndexedAsset, ...]

    @classmethod
    def from_catalog(cls, catalog_path: Path, bundle_root: Path,
                     version_timestamp: int) -> "AssetIndex":
        catalog_path, bundle_root = Path(catalog_path), Path(bundle_root)
        payload: Any = json.loads(catalog_path.read_text(encoding="utf-8-sig"))
        catalog_version = payload.get("version_timestamp") if isinstance(payload, dict) else None
        if catalog_version is not None and int(catalog_version) != int(version_timestamp):
            raise ValueError(f"catalog version {catalog_version} does not match requested {version_timestamp}")
        if isinstance(payload, list):
            source_records = payload
        elif isinstance(payload, dict):
            source_records = payload.get("assets", ())
        else:
            raise ValueError("AssetStudio catalog must be an object or an assets array")
        records = []
        for number, raw in enumerate(source_records, start=1):
            if not isinstance(raw, dict):
                continue
            source_path = str(raw.get("Source", raw.get("source", "")))
            source_name = ntpath.basename(source_path.replace("/", "\\"))
            source_stem = source_name[:-7] if source_name.casefold().endswith(".bundle") else source_name
            bundle_hash = str(raw.get("bundle_hash", source_stem)).strip().casefold()
            path = bundle_root / bundle_hash
            if not path.is_file():
                path = bundle_root / f"{bundle_hash}.bundle"
            path_id_value = raw.get("path_id", raw.get("PathID"))
            if path_id_value is None or not bundle_hash:
                continue
            records.append(IndexedAsset(
                name=str(raw.get("name", raw.get("Name", ""))),
                container=str(raw.get("container", raw.get("Container", ""))).replace("\\", "/").strip("/"),
                bundle_hash=bundle_hash,
                path_id=int(path_id_value),
                dependencies=tuple(raw.get("dependencies", raw.get("Dependencies", ()))),
                version_timestamp=int(version_timestamp), bundle_path=path,
                bundle_present=bool(bundle_hash) and path.is_file(),
                evidence=f"{catalog_path}#/assets/{number - 1}",
                asset_type=str(raw.get("asset_type", raw.get("Type", ""))),
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

    def find_by_name_exact(self, name: str) -> tuple[IndexedAsset, ...]:
        """Find configured prefab names exactly; callers must handle ambiguity."""
        wanted = str(name)
        return tuple(asset for asset in self.assets
                     if asset.version_timestamp == self.version_timestamp and asset.name == wanted)

    def find_by_container_exact(self, name_or_path: str) -> tuple[IndexedAsset, ...]:
        """Find exact AssetStudio container matches for a configured Prefab.

        Game configuration typically stores the Prefab leaf name while the
        AssetStudio map stores its full container path. A leaf-only query is
        exact on that basename and callers must reject matches across bundles.
        """
        wanted = str(name_or_path).replace("\\", "/").strip("/")
        full_path = "/" in wanted
        folded = wanted.casefold()
        return tuple(
            asset for asset in self.assets
            if asset.version_timestamp == self.version_timestamp
            and (
                asset.container.casefold() == folded
                if full_path
                else PurePosixPath(asset.container).name.casefold() == folded
            )
        )

    def bundle_path_for(self, bundle_hash: str) -> Path | None:
        matches = {asset.bundle_path for asset in self.assets
                   if asset.version_timestamp == self.version_timestamp
                   and asset.bundle_hash.casefold() == str(bundle_hash).casefold()}
        if not matches:
            return None
        if len(matches) > 1:
            raise ValueError(f"ambiguous bundle path for hash {bundle_hash}")
        return next(iter(matches))

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
