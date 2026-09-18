"""Discover Spine preview resources and match them to character identities."""

from __future__ import annotations

import hashlib
import json
import os
import re
from collections.abc import Mapping
from pathlib import Path

from .character_names import display_skin_number
from .resource_model import PreviewResourceCatalog, SpineSkinRecord
from .spine_adapter import SkinQueryResult, SpineQueryRunner


_CHARACTER_ID_KEYS = ("character_id", "characterId", "char_id", "charId", "role_id", "roleId", "id")
_SPINE_SKEL_SUFFIXES = (".skel.prefab", ".skel.bytes", ".skel")


def _spine_logical_stem(path) -> str:
    """Return a Spine stem without AssetStudio/Unity physical suffixes."""
    name = Path(path).name
    lowered = name.casefold()
    for suffix in _SPINE_SKEL_SUFFIXES:
        if lowered.endswith(suffix):
            return name[: -len(suffix)]
    return Path(name).stem


def display_skin_name(skel_path, character_id, resource_family) -> str:
    """Build a compact source-skin label, never exposing internal skin names."""
    skin_number = display_skin_number(skel_path, character_id, resource_family)
    if resource_family in {"cardspine", "battlespine"}:
        return f"皮肤 {skin_number}" if skin_number.isdigit() else skin_number
    if resource_family == "eventcovers":
        return f"活动封面 {skin_number}"
    return skin_number or "未命名皮肤"


def _resource_family(path) -> str:
    normalized = str(path).replace("\\", "/").casefold()
    if "/eventcovers/" in normalized or Path(path).name.casefold().startswith("eventcovers_"):
        return "eventcovers"
    if "/cardspine/" in normalized or Path(path).name.casefold().startswith("cardspine_"):
        return "cardspine"
    if "/battlespine/" in normalized or Path(path).name.casefold().startswith("battlespine_"):
        return "battlespine"
    return "spine"


def _value_from_metadata(metadata, key):
    if isinstance(metadata, Mapping):
        return metadata.get(key)
    return getattr(metadata, key, None)


def _as_character_id(value):
    if value is None or isinstance(value, bool):
        return None
    value = str(value).strip()
    return value or None


def _known_character_ids(metadata) -> set[str]:
    if not isinstance(metadata, Mapping):
        return set()
    known = set()
    for key in ("characters", "character_data", "characterData", "ids"):
        value = metadata.get(key)
        if isinstance(value, Mapping):
            known.update(str(item).strip() for item in value if str(item).strip())
        elif isinstance(value, (list, tuple, set)):
            for item in value:
                candidate = item if not isinstance(item, Mapping) else next(
                    (_value_from_metadata(item, field) for field in _CHARACTER_ID_KEYS), None
                )
                candidate = _as_character_id(candidate)
                if candidate:
                    known.add(candidate)
    for key, value in metadata.items():
        if str(key).isdigit():
            known.add(str(key))
        if key in _CHARACTER_ID_KEYS:
            candidate = _as_character_id(value)
            if candidate:
                known.add(candidate)
    return known


def resolve_character_id(path, metadata) -> str | None:
    """Resolve a character ID from explicit metadata or one reliable path ID."""
    path_text = os.fspath(path)
    path_variants = {
        path_text,
        os.path.abspath(path_text),
        path_text.replace("\\", "/"),
        os.path.basename(path_text),
    }

    if isinstance(metadata, Mapping):
        for key in path_variants:
            if key in metadata:
                value = metadata[key]
                if isinstance(value, Mapping):
                    value = next((_value_from_metadata(value, field) for field in _CHARACTER_ID_KEYS), None)
                candidate = _as_character_id(value)
                if candidate:
                    return candidate
        for key in ("path_to_character", "pathToCharacter", "resources", "by_path"):
            entries = metadata.get(key)
            if isinstance(entries, Mapping):
                for variant in path_variants:
                    value = entries.get(variant)
                    if isinstance(value, Mapping):
                        value = next((_value_from_metadata(value, field) for field in _CHARACTER_ID_KEYS), None)
                    candidate = _as_character_id(value)
                    if candidate:
                        return candidate

    for key in _CHARACTER_ID_KEYS:
        candidate = _as_character_id(_value_from_metadata(metadata, key))
        if candidate:
            return candidate

    source_name = Path(path_text).name
    stem = _spine_logical_stem(source_name)
    convention = re.fullmatch(
        r"(?:cardspine|battlespine)[_-](\d{5})(?:[_-]\d+)?(?:_bg)?",
        stem,
        flags=re.IGNORECASE,
    )
    candidates = [convention.group(1)] if convention else []
    parent_name = Path(path_text).parent.name
    if re.fullmatch(r"\d{5}", parent_name):
        candidates.append(parent_name)
    unique_candidates = set(candidates)
    if len(unique_candidates) != 1:
        return None
    candidate = next(iter(unique_candidates))
    known_ids = _known_character_ids(metadata)
    return candidate if not known_ids or candidate in known_ids else None


def _paired_atlas_path(skel_path: Path) -> Path:
    """Find the atlas beside normal and Unity-exported Spine files."""
    stem = _spine_logical_stem(skel_path)
    suffix = ".prefab" if skel_path.name.casefold().endswith(".skel.prefab") else ""
    candidates = tuple(
        dict.fromkeys(
            (
                f"{stem}.atlas{suffix}",
                f"{stem}.atlas",
                f"{stem}.atlas.txt",
            )
        )
    )
    for candidate in candidates:
        path = skel_path.with_name(candidate)
        if path.is_file():
            return path
    return skel_path.with_name(candidates[0])


def _metadata_for_path(character_data, path):
    if not isinstance(character_data, Mapping):
        return character_data
    variants = {
        str(path),
        os.path.abspath(str(path)),
        str(path).replace("\\", "/"),
        path.name,
    }
    for key in variants:
        if key in character_data:
            return character_data[key]
    return character_data


def _source_skin_identity(skel_path, atlas_path, skin_name):
    try:
        source_skel = os.path.abspath(skel_path).replace("\\", "/")
        source_atlas = os.path.abspath(atlas_path).replace("\\", "/")
    except ValueError:
        source_skel = str(skel_path).replace("\\", "/")
        source_atlas = str(atlas_path).replace("\\", "/")
    identity = {
        "source_skel": source_skel,
        "atlas_path": source_atlas,
        "skin_name": skin_name,
    }
    payload = json.dumps(identity, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _result_skin_names(result):
    if isinstance(result, SkinQueryResult):
        return result.skin_names
    names = getattr(result, "skin_names", None)
    if names is None:
        names = getattr(result, "skins", result)
    if isinstance(names, Mapping):
        names = names.keys()
    if isinstance(names, str):
        names = (names,)
    normalized = []
    seen = set()
    for name in names:
        value = str(name).strip()
        if value and value not in seen:
            normalized.append(value)
            seen.add(value)
    return tuple(normalized)


def _query_ok(result):
    if isinstance(result, SkinQueryResult):
        return result.ok
    return getattr(result, "returncode", 0) == 0 and not getattr(result, "error", "")


def _query_fingerprint(result, skin_name):
    fingerprints = getattr(result, "attachment_fingerprints", {}) or {}
    return fingerprints.get(skin_name)


def _query_identity_fingerprint(result, skin_name):
    fingerprints = getattr(result, "identity_fingerprints", {}) or {}
    return fingerprints.get(skin_name)


def _query_diagnostic(result):
    parts = []
    for value in (
        getattr(result, "diagnostic", ""),
        getattr(result, "error", ""),
        f"exit code {result.returncode}" if getattr(result, "returncode", 0) not in (0, None) else "",
        getattr(result, "stderr", ""),
    ):
        value = str(value or "").strip()
        if value and value not in parts:
            parts.append(value)
    return "; ".join(parts)


def discover_preview_resources(material_dir, character_data=None, query_runner=None) -> PreviewResourceCatalog:
    """Discover same-directory Spine pairs and return identity-based skin records."""
    root = Path(material_dir)
    if not root.is_dir():
        return PreviewResourceCatalog.from_records(())
    runner = query_runner or SpineQueryRunner()
    records = []

    skel_paths = {
        path
        for pattern in ("*.skel", "*.skel.bytes", "*.skel.prefab")
        for path in root.rglob(pattern)
        if path.is_file()
    }
    for skel_path in sorted(skel_paths, key=lambda item: str(item).casefold()):
        atlas_path = _paired_atlas_path(skel_path)
        metadata = _metadata_for_path(character_data, skel_path)
        character_id = resolve_character_id(str(skel_path), metadata)
        resource_family = _resource_family(skel_path)

        if not atlas_path.is_file():
            records.append(
                SpineSkinRecord(
                    character_id=character_id,
                    source_skel=str(skel_path),
                    atlas_path=str(atlas_path),
                    skin_name="",
                    attachment_fingerprint="",
                    display_name=display_skin_name(skel_path, character_id, resource_family),
                    status="invalid",
                    identity_fingerprint=_source_skin_identity(str(skel_path), str(atlas_path), ""),
                    fingerprint_kind="source_skin_identity",
                    diagnostic=f"atlas missing: {atlas_path}",
                    resource_family=resource_family,
                )
            )
            continue

        result = runner.query_skins(str(skel_path), str(atlas_path))
        skin_names = _result_skin_names(result)
        if not _query_ok(result) or not skin_names:
            records.append(
                SpineSkinRecord(
                    character_id=character_id,
                    source_skel=str(skel_path),
                    atlas_path=str(atlas_path),
                    skin_name="",
                    attachment_fingerprint="",
                    display_name=display_skin_name(skel_path, character_id, resource_family),
                    status="invalid",
                    identity_fingerprint=_query_identity_fingerprint(result, "")
                    or _source_skin_identity(str(skel_path), str(atlas_path), ""),
                    fingerprint_kind="source_skin_identity",
                    diagnostic=_query_diagnostic(result)
                    or "skin query did not return any skin names",
                    resource_family=resource_family,
                )
            )
            continue

        for skin_name in skin_names:
            attachment_fingerprint = _query_fingerprint(result, skin_name) or ""
            identity_fingerprint = _query_identity_fingerprint(result, skin_name)
            diagnostic = _query_diagnostic(result)
            if not attachment_fingerprint:
                fallback_diagnostic = (
                    "attachment set fingerprint unavailable; using source/skin identity fallback"
                )
                diagnostic = "; ".join(value for value in (diagnostic, fallback_diagnostic) if value)
            records.append(
                SpineSkinRecord(
                    character_id=character_id,
                    source_skel=str(skel_path),
                    atlas_path=str(atlas_path),
                    skin_name=skin_name,
                    attachment_fingerprint=attachment_fingerprint,
                    display_name=display_skin_name(skel_path, character_id, resource_family),
                    status="ready",
                    identity_fingerprint=identity_fingerprint
                    or _source_skin_identity(str(skel_path), str(atlas_path), skin_name),
                    fingerprint_kind=("attachment_set" if attachment_fingerprint else "source_skin_identity"),
                    diagnostic=diagnostic,
                    resource_family=resource_family,
                )
            )

    return PreviewResourceCatalog.from_records(records)
