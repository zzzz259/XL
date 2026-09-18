"""Publish source Spine resource groups into the final output directory."""

from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
from collections import OrderedDict
from dataclasses import dataclass
from dataclasses import asdict
from pathlib import Path

from .resource_model import PreviewResourceCatalog, SpineSkinRecord


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


def _source_key(source_skel: Path, material_dir: Path) -> str:
    try:
        relative = source_skel.resolve().relative_to(material_dir.resolve())
        value = relative.as_posix()
    except ValueError:
        value = _normalise_path(source_skel)
    digest = hashlib.sha256(value.casefold().encode("utf-8")).hexdigest()[:12]
    return f"{_safe_name(source_skel.stem)}_{digest}"


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
    all_records = [*catalog.skins.values(), *catalog.unmatched]
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


def publish_raw_spine_resources(
    catalog: PreviewResourceCatalog,
    material_dir: str | os.PathLike[str],
    output_root: str | os.PathLike[str],
) -> RawSpinePublishSummary:
    """Copy each discovered Spine source group to ``output/spine``.

    The source tree remains untouched. A source group is copied once even when
    the Spine file exposes multiple internal skins. The small JSON index is
    written atomically so a failed copy cannot leave a partially written index.
    """
    material = Path(material_dir)
    output = Path(output_root)
    spine_output = output / "spine"
    diagnostics: list[str] = []
    published = 0
    copied_files = 0
    skipped = 0
    index_entries: list[dict[str, object]] = []

    for source_text, (character_id, records) in _record_groups(catalog).items():
        source_skel = Path(source_text)
        if not source_skel.is_file():
            skipped += 1
            diagnostics.append(f"Spine source missing: {source_skel}")
            continue
        destination = spine_output / _safe_name(character_id or "unmatched", "unmatched") / _source_key(
            source_skel, material
        )
        destination.mkdir(parents=True, exist_ok=True)
        files_for_index: list[str] = []

        target_skel = destination / source_skel.name
        if _copy_if_present(source_skel, target_skel):
            copied_files += 1
            files_for_index.append(target_skel.relative_to(output).as_posix())

        atlas_path = Path(records[0].atlas_path) if records and records[0].atlas_path else None
        if atlas_path and atlas_path.is_file():
            target_atlas = destination / atlas_path.name
            if _copy_if_present(atlas_path, target_atlas):
                copied_files += 1
                files_for_index.append(target_atlas.relative_to(output).as_posix())
            for texture_name in _atlas_texture_names(atlas_path):
                texture_source = atlas_path.parent / Path(texture_name)
                texture_target = destination / Path(texture_name)
                if _copy_if_present(texture_source, texture_target):
                    copied_files += 1
                    files_for_index.append(texture_target.relative_to(output).as_posix())
                else:
                    diagnostics.append(
                        f"Spine atlas texture missing: {texture_source} (source: {source_skel})"
                    )
        else:
            diagnostics.append(f"Spine atlas missing: {atlas_path or source_skel.with_suffix('.atlas')}")

        index_entries.append(
            {
                "character_id": character_id,
                "source_key": destination.name,
                "source_skel": str(source_skel),
                "files": sorted(set(files_for_index)),
                "skins": sorted({record.skin_name for record in records if record.skin_name}),
                "records": [asdict(record) for record in records],
            }
        )
        published += 1

    index_path = output / _INDEX_NAME
    output.mkdir(parents=True, exist_ok=True)
    payload = {"version": 1, "spine": index_entries}
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
