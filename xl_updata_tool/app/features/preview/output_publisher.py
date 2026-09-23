"""Publish source Spine resource groups into the final output directory."""

from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
from collections import OrderedDict
from dataclasses import asdict, dataclass, replace
from pathlib import Path

from .resource_catalog import discover_preview_resources
from .resource_model import PreviewResourceCatalog, SpineSkinRecord
from .spine_adapter import SpineQueryRunner


_IMAGE_SUFFIXES = frozenset({".bmp", ".gif", ".jpeg", ".jpg", ".png", ".tga", ".webp"})
_INDEX_NAME = "preview_index.json"


@dataclass(frozen=True, slots=True)
class RawSpinePublishSummary:
    published: int = 0
    copied_files: int = 0
    skipped: int = 0
    diagnostics: tuple[str, ...] = ()


def _safe_name(value: str, fallback: str = "resource") -> str:
    cleaned = re.sub(r"[^A-Za-z0-9._-]+", "_", str(value).strip()).strip(" .")
    return cleaned or fallback


def _normalise_path(path: Path) -> str:
    return os.path.normcase(os.path.abspath(os.fspath(path))).replace("\\", "/")


def _group_fingerprint(source_skel: Path, atlas_path: Path | None) -> str:
    digest = hashlib.sha256()
    paths = [source_skel]
    if atlas_path is not None:
        paths.append(atlas_path)
        paths.extend(atlas_path.parent / name for name in _atlas_texture_names(atlas_path))
    for path in paths:
        digest.update(path.name.casefold().encode("utf-8", errors="replace"))
        digest.update(b"\0")
        if not path.is_file():
            digest.update(b"missing\0")
            continue
        with path.open("rb") as source:
            for chunk in iter(lambda: source.read(1024 * 1024), b""):
                digest.update(chunk)
        digest.update(b"\0")
    return digest.hexdigest()[:16]


def _source_key(source_skel: Path, material_dir: Path, atlas_path: Path | None) -> str:
    try:
        relative = source_skel.resolve().relative_to(material_dir.resolve())
        value = relative.as_posix()
    except ValueError:
        value = _normalise_path(source_skel)
    path_digest = hashlib.sha256(value.casefold().encode("utf-8")).hexdigest()[:10]
    content_digest = _group_fingerprint(source_skel, atlas_path)
    return f"{_safe_name(source_skel.stem)}_{path_digest}_{content_digest}"


def _logical_spine_name(path: Path, suffix: str) -> str:
    """Drop Unity/AssetStudio transport suffixes from published filenames."""
    name = path.name
    lowered = name.casefold()
    for physical_suffix in (f".{suffix}.prefab", f".{suffix}.bytes", f".{suffix}.txt"):
        if lowered.endswith(physical_suffix):
            return f"{name[:-len(physical_suffix)]}.{suffix}"
    if lowered.endswith(f".{suffix}"):
        return name
    return f"{path.stem}.{suffix}"


def _published_skel_name(path: Path) -> str:
    return _logical_spine_name(path, "skel")


def _published_atlas_name(path: Path) -> str:
    return _logical_spine_name(path, "atlas")


def _remove_physical_suffix_aliases(destination: Path, path: Path, suffix: str, target_name: str) -> None:
    logical_name = _logical_spine_name(path, suffix).rsplit(".", 1)[0]
    for candidate in destination.glob(f"{logical_name}.{suffix}.*"):
        if candidate.name == target_name or not candidate.is_file():
            continue
        try:
            candidate.unlink()
        except OSError:
            pass


def _atlas_texture_names(atlas_path: Path) -> tuple[str, ...]:
    """Read page names from both .atlas and Unity .atlas.txt files."""
    if not atlas_path.is_file():
        return ()
    try:
        lines = atlas_path.read_text(encoding="utf-8", errors="replace").splitlines()
    except OSError:
        return ()
    names: list[str] = []
    seen: set[str] = set()
    for raw_line in lines:
        line = raw_line.strip()
        if not line or ":" in line:
            continue
        candidate = Path(line)
        if candidate.suffix.casefold() not in _IMAGE_SUFFIXES:
            continue
        normalized = candidate.as_posix()
        if normalized not in seen:
            names.append(normalized)
            seen.add(normalized)
    return tuple(names)


def _record_groups(catalog: PreviewResourceCatalog) -> OrderedDict[str, tuple[str | None, list[SpineSkinRecord]]]:
    groups: OrderedDict[str, tuple[str | None, list[SpineSkinRecord]]] = OrderedDict()
    all_records = list(catalog.skins.values())
    for record in all_records:
        source = _normalise_path(Path(record.source_skel))
        if source not in groups:
            groups[source] = (record.character_id, [record])
        else:
            character_id, records = groups[source]
            if not character_id and record.character_id:
                character_id = record.character_id
            records.append(record)
            groups[source] = (character_id, records)
    return groups


def _copy_if_present(source: Path, target: Path) -> bool:
    if not source.is_file():
        return False
    target.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(source, target)
    return True


def _archive_files(directory: Path, output_root: Path) -> list[str]:
    return sorted(
        path.relative_to(output_root).as_posix()
        for path in directory.rglob("*")
        if path.is_file()
    )


def _matching_archive_file(directory: Path, source_path: str, suffix: str) -> Path | None:
    source_name = Path(source_path).name.casefold()
    candidates = [path for path in directory.rglob(f"*{suffix}") if path.is_file()]
    return next((path for path in candidates if path.name.casefold() == source_name), None) or (
        candidates[0] if len(candidates) == 1 else None
    )


def _remap_index_entry(entry: object, output_root: Path, spine_output: Path) -> dict | None:
    if not isinstance(entry, dict):
        return None
    family = _safe_name(str(entry.get("resource_family") or "spine"), "spine")
    source_key = str(entry.get("source_key") or "")
    if not source_key:
        return None
    directory = spine_output / family / source_key
    if not directory.is_dir():
        return None
    source_records = entry.get("records")
    if not isinstance(source_records, list):
        source_records = []
    records = []
    for source_record in source_records:
        if not isinstance(source_record, dict):
            continue
        skel_path = _matching_archive_file(directory, str(source_record.get("source_skel") or ""), ".skel")
        atlas_path = _matching_archive_file(directory, str(source_record.get("atlas_path") or ""), ".atlas")
        if skel_path is None:
            continue
        records.append(
            {
                **source_record,
                "source_skel": str(skel_path.resolve()),
                "atlas_path": str(atlas_path.resolve()) if atlas_path else source_record.get("atlas_path", ""),
            }
        )
    if not records:
        return None
    skel_path = _matching_archive_file(directory, str(entry.get("source_skel") or ""), ".skel")
    return {
        **entry,
        "source_key": source_key,
        "source_skel": str(skel_path.resolve()) if skel_path else records[0]["source_skel"],
        "files": _archive_files(directory, output_root),
        "records": records,
    }


def _recover_unindexed_archive_groups(
    spine_output: Path,
    output_root: Path,
    indexed_keys: set[tuple[str, str]],
    query_runner,
    diagnostics: list[str],
) -> list[dict]:
    recovered: list[dict] = []
    if not spine_output.is_dir():
        return recovered
    for family_dir in sorted((path for path in spine_output.iterdir() if path.is_dir()), key=lambda p: p.name.casefold()):
        family = family_dir.name
        for group_dir in sorted((path for path in family_dir.iterdir() if path.is_dir()), key=lambda p: p.name.casefold()):
            identity = (family.casefold(), group_dir.name.casefold())
            if identity in indexed_keys:
                continue
            if not any(group_dir.glob("*.skel")):
                continue
            catalog = discover_preview_resources(group_dir, query_runner=query_runner)
            for _, (character_id, records) in _record_groups(catalog).items():
                if not records:
                    continue
                source_skel = Path(records[0].source_skel)
                atlas_path = Path(records[0].atlas_path) if records[0].atlas_path else None
                entry = {
                    "character_id": character_id,
                    "resource_family": records[0].resource_family or family,
                    "source_key": group_dir.name,
                    "source_skel": str(source_skel.resolve()),
                    "files": _archive_files(group_dir, output_root),
                    "skins": sorted({record.skin_name for record in records if record.skin_name}),
                    "records": [
                        asdict(
                            replace(
                                record,
                                source_skel=str(Path(record.source_skel).resolve()),
                                atlas_path=(
                                    str(Path(record.atlas_path).resolve())
                                    if atlas_path and atlas_path.is_file()
                                    else record.atlas_path
                                ),
                            )
                        )
                        for record in records
                    ],
                }
                recovered.append(entry)
                indexed_keys.add(identity)
            if not catalog.skins:
                diagnostics.append(f"Unable to recover Spine index entry: {group_dir}")
    return recovered


def publish_raw_spine_resources(
    catalog: PreviewResourceCatalog,
    material_dir: str | os.PathLike[str],
    output_root: str | os.PathLike[str],
    query_runner=None,
) -> RawSpinePublishSummary:
    """Copy each discovered Spine source group to ``output/spine``.

    The source tree remains untouched. A source group is copied once even when
    the Spine file exposes multiple internal skins. Resource folders include
    both source-path and content fingerprints, and the index accumulates archived
    groups instead of replacing entries from earlier imports.
    """
    material = Path(material_dir)
    output = Path(output_root)
    spine_output = output / "spine"
    diagnostics: list[str] = []
    published = 0
    copied_files = 0
    skipped = 0
    new_index_entries: list[dict[str, object]] = []

    for source_text, (character_id, records) in _record_groups(catalog).items():
        source_skel = Path(source_text)
        if not source_skel.is_file():
            skipped += 1
            diagnostics.append(f"Spine source missing: {source_skel}")
            continue
        resource_family = records[0].resource_family if records else "spine"
        atlas_path = Path(records[0].atlas_path) if records and records[0].atlas_path else None
        destination = spine_output / _safe_name(resource_family, "spine") / _source_key(
            source_skel, material, atlas_path
        )
        destination.mkdir(parents=True, exist_ok=True)
        target_skel = destination / _published_skel_name(source_skel)
        _remove_physical_suffix_aliases(destination, source_skel, "skel", target_skel.name)
        if _copy_if_present(source_skel, target_skel):
            copied_files += 1

        target_atlas = destination / _published_atlas_name(atlas_path) if atlas_path else None
        if atlas_path and atlas_path.is_file():
            target_atlas = destination / _published_atlas_name(atlas_path)
            _remove_physical_suffix_aliases(destination, atlas_path, "atlas", target_atlas.name)
            if _copy_if_present(atlas_path, target_atlas):
                copied_files += 1
            for texture_name in _atlas_texture_names(atlas_path):
                texture_source = atlas_path.parent / Path(texture_name)
                texture_target = destination / Path(texture_name)
                if _copy_if_present(texture_source, texture_target):
                    copied_files += 1
                else:
                    diagnostics.append(
                        f"Spine atlas texture missing: {texture_source} (source: {source_skel})"
                    )
        else:
            diagnostics.append(f"Spine atlas missing: {atlas_path or source_skel.with_suffix('.atlas')}")

        archived_records = [
            replace(
                record,
                source_skel=str(target_skel.resolve()),
                atlas_path=(str(target_atlas.resolve()) if target_atlas else record.atlas_path),
            )
            for record in records
        ]
        new_index_entries.append(
            {
                "character_id": character_id,
                "resource_family": resource_family,
                "source_key": destination.name,
                "source_skel": str(target_skel.resolve()),
                "files": _archive_files(destination, output),
                "skins": sorted({record.skin_name for record in records if record.skin_name}),
                "records": [asdict(record) for record in archived_records],
            }
        )
        published += 1

    output.mkdir(parents=True, exist_ok=True)
    index_path = output / _INDEX_NAME
    index_entries_by_key: dict[tuple[str, str], dict] = {}
    if index_path.is_file():
        try:
            previous_payload = json.loads(index_path.read_text(encoding="utf-8"))
            previous_entries = previous_payload.get("spine", ()) if isinstance(previous_payload, dict) else ()
            for previous_entry in previous_entries:
                remapped = _remap_index_entry(previous_entry, output, spine_output)
                if remapped:
                    identity = (
                        str(remapped.get("resource_family") or "spine").casefold(),
                        str(remapped.get("source_key") or "").casefold(),
                    )
                    index_entries_by_key[identity] = remapped
        except (OSError, UnicodeError, json.JSONDecodeError, TypeError) as error:
            diagnostics.append(f"previous preview index could not be read: {error}")

    for entry in new_index_entries:
        identity = (
            str(entry.get("resource_family") or "spine").casefold(),
            str(entry.get("source_key") or "").casefold(),
        )
        index_entries_by_key[identity] = entry

    runner = query_runner or SpineQueryRunner()
    recovered_entries = _recover_unindexed_archive_groups(
        spine_output,
        output,
        set(index_entries_by_key),
        runner,
        diagnostics,
    )
    for entry in recovered_entries:
        identity = (
            str(entry.get("resource_family") or "spine").casefold(),
            str(entry.get("source_key") or "").casefold(),
        )
        index_entries_by_key[identity] = entry

    ordered_entries = [
        index_entries_by_key[key]
        for key in sorted(index_entries_by_key)
    ]
    payload = {"version": 1, "spine": ordered_entries}
    temporary = index_path.with_name(f"{index_path.name}.tmp")
    try:
        temporary.write_text(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True), encoding="utf-8")
        os.replace(temporary, index_path)
    except OSError as error:
        diagnostics.append(f"preview index write failed: {error}")
        try:
            temporary.unlink()
        except OSError:
            pass

    return RawSpinePublishSummary(
        published=published,
        copied_files=copied_files,
        skipped=skipped,
        diagnostics=tuple(diagnostics),
    )
