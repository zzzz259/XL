"""Version-bound index for AssetStudio catalog entries."""

from __future__ import annotations

import json
import ntpath
import re
from dataclasses import dataclass, field
from pathlib import Path
from pathlib import PurePosixPath
from types import MappingProxyType
from typing import Any, Mapping


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
    bundle_root: Path | None = field(default=None, repr=False, compare=False)
    cab_cache_path: Path | None = field(default=None, repr=False, compare=False)
    _discovered: dict[tuple[str, int], IndexedAsset] = field(default_factory=dict, init=False, repr=False, compare=False)
    _cab_paths: dict = field(default_factory=dict, init=False, repr=False, compare=False)
    _trees: dict = field(default_factory=dict, init=False, repr=False, compare=False)
    _exact: Mapping[tuple[str, int], tuple[IndexedAsset, ...]] = field(init=False, repr=False, compare=False)
    _names: Mapping[str, tuple[IndexedAsset, ...]] = field(init=False, repr=False, compare=False)
    _containers: Mapping[str, tuple[IndexedAsset, ...]] = field(init=False, repr=False, compare=False)
    _leaves: Mapping[str, tuple[IndexedAsset, ...]] = field(init=False, repr=False, compare=False)
    _bundle_paths: Mapping[str, frozenset[Path]] = field(init=False, repr=False, compare=False)
    _resolved: Mapping[tuple[str, str], tuple[IndexedAsset, ...]] = field(init=False, repr=False, compare=False)
    _path_ids: Mapping[int, tuple[IndexedAsset, ...]] = field(init=False, repr=False, compare=False)
    _serialized_objects: dict[Path, dict[tuple[str, int], str]] = field(default_factory=dict, init=False, repr=False, compare=False)
    _external_matches: dict[tuple[str, int], tuple[IndexedAsset, ...]] = field(default_factory=dict, init=False, repr=False, compare=False)
    _object_pointers: dict[tuple[str, int], tuple[Any, ...]] = field(default_factory=dict, init=False, repr=False, compare=False)

    def __post_init__(self) -> None:
        exact, names, containers, leaves, bundle_paths, resolved, path_ids = {}, {}, {}, {}, {}, {}, {}
        for asset in self.assets:
            container = asset.container.casefold()
            name_leaf = asset.name.replace("\\", "/").rsplit("/", 1)[-1].casefold()
            # resolve() historically matches the complete catalog, whereas the
            # explicit lookup methods only match this index's version.
            resolved.setdefault((container, name_leaf), []).append(asset)
            if asset.version_timestamp != self.version_timestamp:
                continue
            bundle_hash = asset.bundle_hash.casefold()
            exact.setdefault((bundle_hash, asset.path_id), []).append(asset)
            path_ids.setdefault(asset.path_id, []).append(asset)
            names.setdefault(asset.name, []).append(asset)
            containers.setdefault(container, []).append(asset)
            leaves.setdefault(PurePosixPath(asset.container).name.casefold(), []).append(asset)
            bundle_paths.setdefault(bundle_hash, set()).add(asset.bundle_path)

        # Freeze both levels so callers cannot alter another lookup's results.
        # These tables belong to this instance, never to a process-wide cache.
        for attribute, groups in (
            ("_exact", exact), ("_names", names), ("_containers", containers),
            ("_leaves", leaves), ("_resolved", resolved),
            ("_path_ids", path_ids),
        ):
            object.__setattr__(self, attribute, MappingProxyType({
                key: tuple(matches) for key, matches in groups.items()
            }))
        object.__setattr__(self, "_bundle_paths", MappingProxyType({
            key: frozenset(paths) for key, paths in bundle_paths.items()
        }))

    @classmethod
    def from_catalog(cls, catalog_path: Path, bundle_root: Path,
                     version_timestamp: int, *, cab_cache_path: Path | None = None) -> "AssetIndex":
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
        # One directory snapshot avoids repeated missing-path stat calls for
        # every object row, especially when only selected ABs are local.
        bundle_files = {p.name.casefold(): p for p in bundle_root.iterdir() if p.is_file()} if bundle_root.is_dir() else {}
        records = []
        for number, raw in enumerate(source_records, start=1):
            if not isinstance(raw, dict):
                continue
            source_path = str(raw.get("Source", raw.get("source", "")))
            source_name = ntpath.basename(source_path.replace("/", "\\"))
            source_stem = source_name[:-7] if source_name.casefold().endswith(".bundle") else source_name
            bundle_hash = str(raw.get("bundle_hash", source_stem)).strip().casefold()
            path = bundle_files.get(bundle_hash) or bundle_files.get(f"{bundle_hash}.bundle")
            present = path is not None
            path = path or bundle_root / f"{bundle_hash}.bundle"
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
                bundle_present=bool(bundle_hash) and present,
                evidence=f"{catalog_path}#/assets/{number - 1}",
                asset_type=str(raw.get("asset_type", raw.get("Type", ""))),
            ))
        return cls(int(version_timestamp), tuple(records), bundle_root, cab_cache_path)

    def find_exact(self, bundle_hash: str, path_id: int) -> tuple[IndexedAsset, ...]:
        """Return exact version-bound matches without guessing by asset name."""
        key = (str(bundle_hash).casefold(), int(path_id))
        return self._exact.get(key, ()) or ((self._discovered[key],) if key in self._discovered else ())

    def has_bundle(self, bundle_hash: str) -> bool:
        return str(bundle_hash).casefold() in self._bundle_paths or any(k[0] == str(bundle_hash).casefold() for k in self._discovered)

    def find_by_name_exact(self, name: str) -> tuple[IndexedAsset, ...]:
        """Find configured prefab names exactly; callers must handle ambiguity."""
        return self._names.get(str(name), ())

    def find_by_container_exact(self, name_or_path: str) -> tuple[IndexedAsset, ...]:
        """Find exact AssetStudio container matches for a configured Prefab.

        Game configuration typically stores the Prefab leaf name while the
        AssetStudio map stores its full container path. A leaf-only query is
        exact on that basename and callers must reject matches across bundles.
        """
        wanted = str(name_or_path).replace("\\", "/").strip("/")
        lookup = self._containers if "/" in wanted else self._leaves
        return lookup.get(wanted.casefold(), ())

    def bundle_path_for(self, bundle_hash: str) -> Path | None:
        matches = self._bundle_paths.get(str(bundle_hash).casefold(), frozenset())
        if not matches:
            matches = frozenset(asset.bundle_path for (bundle, _), asset in self._discovered.items() if bundle == str(bundle_hash).casefold())
        if not matches and self.bundle_root is not None and re.fullmatch(r'[0-9a-fA-F]{32}',str(bundle_hash)):
            candidates=(self.bundle_root / str(bundle_hash), self.bundle_root / (str(bundle_hash)+'.bundle'))
            matches=frozenset(path for path in candidates if path.is_file())
        if not matches:
            return None
        if len(matches) > 1:
            raise ValueError(f"ambiguous bundle path for hash {bundle_hash}")
        return next(iter(matches))

    def find_external(self, identifier: str, path_id: int) -> tuple[IndexedAsset, ...]:
        """Verify CAB + object identity inside candidate physical bundles.

        PathID narrows the catalog candidates; it never proves a binding. The
        owning SerializedFile name and actual object type must agree as well.
        Cached metadata contains no UnityPy environments or decoded images.
        """
        cab = canonical_bundle_hash(identifier)
        if cab is None:
            return ()
        key = (cab, int(path_id))
        if key not in self._external_matches:
            matches = []
            for asset in self._path_ids.get(int(path_id), ()):
                if not asset.bundle_present:
                    continue
                identities = self._serialized_identities(asset.bundle_path)
                actual_type = identities.get(key)
                if actual_type is not None and (not asset.asset_type or asset.asset_type == actual_type):
                    matches.append(asset)
            if not matches and self.bundle_root is not None:
                from .cab_index import load_headers
                if 'headers' not in self._cab_paths:
                    self._cab_paths['headers'] = load_headers(self.bundle_root, self.version_timestamp, self.cab_cache_path)
                for path in self._cab_paths['headers'].get(cab, ()):
                    actual_type = self._serialized_identities(path).get(key)
                    if actual_type is None:
                        continue
                    physical_hash = path.stem.casefold()
                    asset = IndexedAsset('', '', physical_hash, int(path_id), (), self.version_timestamp,
                        path, True, f'CAB-{cab}:{path_id}', actual_type)
                    self._discovered[(physical_hash,int(path_id))] = asset
                    matches.append(asset)
            self._external_matches[key] = tuple(matches)
        return self._external_matches[key]

    def _serialized_identities(self, path: Path) -> dict[tuple[str, int], str]:
        if path not in self._serialized_objects:
            from .unity_scene import _load_environment

            identities = {}
            try:
                env = _load_environment(path)
                for obj in env.objects:
                    cab = canonical_bundle_hash(str(getattr(getattr(obj, "assets_file", None), "name", "")))
                    if cab:
                        identities[(cab, int(obj.path_id))] = str(obj.type.name)
            except Exception:
                # Failed verification cannot turn a coincident PathID into a match.
                identities = {}
            self._serialized_objects[path] = identities
        return self._serialized_objects[path]

    def object_references(self, asset: IndexedAsset) -> tuple[Any, ...]:
        """Read and cache only one metadata object's required core PPtrs."""
        from .scope_policy import required_dependency_field
        from .unity_scene import _load_environment, _pointer_records, _typetree

        key = (asset.bundle_hash, asset.path_id)
        if key not in self._object_pointers:
            pointers = ()
            if asset.asset_type in {"Material", "Sprite", "MonoBehaviour"}:
                env = _load_environment(asset.bundle_path)
                matches = [obj for obj in env.objects if int(obj.path_id) == asset.path_id
                           and str(obj.type.name) == asset.asset_type]
                if len(matches) != 1:
                    raise ValueError(f"metadata object identity is absent or ambiguous: {key}")
                obj = matches[0]
                pointers = tuple(pointer for pointer in _pointer_records(
                    {asset.path_id: _typetree(obj)}, {asset.path_id: obj}, asset.bundle_hash,
                ) if required_dependency_field(pointer.field_path))
            self._object_pointers[key] = pointers
        return self._object_pointers[key]

    def object_tree(self, ref):
        return self._read_object(ref)[1]

    def _read_object(self, ref):
        from .unity_scene import _load_environment, _typetree
        key=(ref.bundle_hash,ref.path_id)
        if key not in self._trees:
            path=self.bundle_path_for(ref.bundle_hash)
            if path is None:
                raise ValueError(f'metadata bundle unavailable: {ref.bundle_hash}')
            env=_load_environment(path)
            matches=[o for o in env.objects if int(o.path_id)==ref.path_id]
            if len(matches)!=1:
                raise ValueError(f'metadata object unavailable/ambiguous: {ref}')
            obj=matches[0]
            external=tuple(str(e.path) for e in getattr(getattr(obj,'assets_file',None),'externals',()))
            self._trees[key]=(external,_typetree(obj))
        return self._trees[key]

    def resolve_object_pointer(self, source, pointer, *, expected_type):
        from .model import AssetRef
        if not isinstance(pointer,dict) or not pointer.get('m_PathID'):
            return None
        file_id,path_id=int(pointer.get('m_FileID',0)),int(pointer['m_PathID'])
        if file_id:
            external,_=self._read_object(source)
            if not 0<file_id<=len(external):
                return None
            matches=self.find_external(external[file_id-1],path_id)
        else:
            matches=self.find_exact(source.bundle_hash,path_id)
        if len(matches)!=1 or matches[0].asset_type!=expected_type:
            return None
        return AssetRef(matches[0].bundle_hash,path_id)

    def resolve(self, container: str, name: str) -> IndexedAsset | None:
        normalized = str(container).replace("\\", "/").strip("/").casefold()
        wanted = str(name).replace("\\", "/").rsplit("/", 1)[-1].casefold()
        matches = self._resolved.get((normalized, wanted), ())
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
