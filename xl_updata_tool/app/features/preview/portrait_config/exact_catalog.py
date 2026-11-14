"""Strict, bounded physical object and owner-qualified container catalog."""

from dataclasses import dataclass
import re
from pathlib import Path

SCHEMA = "portrait-exact-catalog/v1"
MAX_BYTES = 16 * 1024 * 1024
MAX_OBJECTS = 100000
MAX_ENTRIES = 10000
MAX_EDGES = 200000


def integer(value, low=0, high=2**31 - 1):
    if type(value) is not int or not low <= value <= high:
        raise ValueError("invalid bounded catalog integer")
    return value


def text(value, *, empty=False):
    if not isinstance(value, str) or len(value) > 4096 or "\x00" in value or (not value and not empty):
        raise ValueError("invalid catalog string")
    return value


def normalized_path(value):
    return text(value).replace("\\", "/").strip("/").casefold()


def wire(value):
    if not isinstance(value, str) or not re.fullmatch("[0-9a-fA-F]{32}", value):
        raise ValueError("invalid physical bundle hash")
    return value.lower()


def cab(value):
    from .asset_index import canonical_bundle_hash

    result = canonical_bundle_hash(text(value))
    if result is None:
        raise ValueError("invalid SerializedFile CAB identity")
    return result


@dataclass(frozen=True, slots=True)
class ContainerEdge:
    role: str
    preload_slot: int | None
    file_id: int
    path_id: int
    external: str
    target: tuple[str, int] | None
    unresolved_reason: str


@dataclass(frozen=True, slots=True)
class ContainerEntry:
    raw_path: str
    path: str
    owner_bundle_hash: str
    owner_serialized_file: str
    asset_bundle_path_id: int
    ordinal: int
    preload_index: int
    preload_size: int
    edges: tuple[ContainerEdge, ...]


def parse_catalog(payload, bundle_root, version):
    from .asset_index import IndexedAsset

    if payload.get("schema") != SCHEMA or integer(payload.get("version_timestamp"), 0, 2**63 - 1) != version:
        raise ValueError("unsupported exact catalog schema/version")
    for name, limit in [("assets", MAX_OBJECTS), ("serialized_files", MAX_ENTRIES), ("container_entries", MAX_ENTRIES)]:
        if not isinstance(payload.get(name), list) or len(payload[name]) > limit:
            raise ValueError("invalid or oversized exact catalog " + name)
    files, cabs, bundles = {}, {}, {}
    for row in payload["serialized_files"]:
        owner = wire(row["bundle_hash"]), cab(row["serialized_file"])
        if owner in files or (owner[0] in bundles and bundles[owner[0]] != owner[1]):
            raise ValueError("unsupported multiple SerializedFiles or duplicate owner")
        externals, preload = row["externals"], row["preload"]
        if (
            not isinstance(externals, list)
            or len(externals) > MAX_ENTRIES
            or not isinstance(preload, list)
            or len(preload) > MAX_EDGES
        ):
            raise ValueError("invalid external/preload table")
        for identifier in externals:
            if text(identifier) != "Resources/unity_builtin_extra":
                cab(identifier)
        for pointer in preload:
            integer(pointer["file_id"], 0, len(externals))
            integer(pointer["path_id"], -(2**63), 2**63 - 1)
        files[owner] = (tuple(externals), tuple(preload))
        bundles[owner[0]] = owner[1]
        cabs.setdefault(owner[1], []).append(owner)
    assets = {}
    for row in payload["assets"]:
        owner = wire(row["bundle_hash"]), cab(row["serialized_file"])
        pid = integer(row["path_id"], -(2**63), 2**63 - 1)
        if not pid or owner not in files or integer(row["version_timestamp"], 0, 2**63 - 1) != version:
            raise ValueError("invalid exact object owner/version/PathID")
        path = Path(bundle_root) / (owner[0] + ".bundle")
        asset = IndexedAsset(
            text(row["name"], empty=True),
            "",
            owner[0],
            pid,
            (),
            version,
            path,
            path.is_file(),
            text(row["source"]),
            text(row["asset_type"]),
            owner[1],
        )
        key = owner[0], pid
        if key in assets and assets[key] != asset:
            raise ValueError("conflicting physical object identity")
        assets[key] = asset

    def edge(pointer, owner, role, slot):
        fid = integer(pointer["file_id"])
        pid = integer(pointer["path_id"], -(2**63), 2**63 - 1)
        external = ""
        target_owner = owner
        externals = files[owner][0]
        if fid > len(externals):
            raise ValueError("PPtr external fileID out of range")
        if fid:
            external = externals[fid - 1]
            if external == "Resources/unity_builtin_extra":
                return ContainerEdge(role, slot, fid, pid, external, None, "null" if not pid else "engine_builtin")
            candidates = cabs.get(cab(external), ())
            if len(candidates) > 1:
                raise ValueError("ambiguous external CAB owner")
            if not candidates:
                return ContainerEdge(
                    role, slot, fid, pid, external, None, "null" if not pid else "external_owner_missing"
                )
            target_owner = candidates[0]
        key = target_owner[0], pid
        reason = "null" if not pid else ""
        if pid and (key not in assets or assets[key].serialized_file != target_owner[1]):
            raise ValueError("PPtr target absent from exact owner")
        return ContainerEdge(role, slot, fid, pid, external, key if pid else None, reason)

    entries, count, ordinals = [], 0, {}
    for row in payload["container_entries"]:
        owner = wire(row["owner_bundle_hash"]), cab(row["owner_serialized_file"])
        if owner not in files:
            raise ValueError("container owner absent")
        abid = integer(row["asset_bundle_path_id"], -(2**63), 2**63 - 1)
        ab = assets.get((owner[0], abid))
        if ab is None or ab.asset_type != "AssetBundle":
            raise ValueError("container AssetBundle identity invalid")
        ordinal = integer(row["ordinal"])
        start, size = integer(row["preload_index"]), integer(row["preload_size"])
        preload = files[owner][1]
        if start > len(preload) or size > len(preload) - start:
            raise ValueError("invalid preload range")
        count += 1 + size
        if count > MAX_EDGES:
            raise ValueError("exact edge limit exceeded")
        edges = (
            edge(row["asset"], owner, "direct", None),
            *(edge(preload[i], owner, "preload", i) for i in range(start, start + size)),
        )
        entry = ContainerEntry(
            text(row["path"]), normalized_path(row["path"]), *owner, abid, ordinal, start, size, edges
        )
        identity = owner, abid, ordinal
        if identity in ordinals and ordinals[identity] != entry:
            raise ValueError("conflicting container ordinal")
        ordinals[identity] = entry
        entries.append(entry)
    return tuple(assets.values()), tuple(entries)
