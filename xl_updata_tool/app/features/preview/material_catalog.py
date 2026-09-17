"""Game-material discovery and export for the preview workbench."""

from __future__ import annotations

import hashlib
import os
import re
import shutil
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from pathlib import Path


_BURST_HEAD_TOKEN = re.compile(
    r"(?<![a-z0-9_-])(?:burst-head|burst_head|bursthead)(?![a-z0-9_-])",
    re.IGNORECASE,
)
_IMAGE_SUFFIXES = frozenset({".bmp", ".gif", ".jpeg", ".jpg", ".png", ".tga", ".webp"})
_ATLAS_SUFFIXES = ("_fui.bank", "_fui.bytes")
_METADATA_KIND_KEYS = frozenset(
    {
        "category",
        "container",
        "kind",
        "material_kind",
        "resource_kind",
        "type",
        "name",
        "path",
        "resource_path",
    }
)


@dataclass(frozen=True, slots=True)
class GameMaterialRecord:
    kind: str
    source_path: str
    display_name: str
    fingerprint: str
    diagnostic: str = ""


@dataclass(frozen=True, slots=True)
class AtlasResourceGroup:
    package_name: str
    source_path: str
    sprite_paths: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class GameMaterialCatalog:
    burst_heads: tuple[GameMaterialRecord, ...]
    atlases: tuple[AtlasResourceGroup, ...]
    unmatched: tuple[GameMaterialRecord, ...]


@dataclass(frozen=True, slots=True)
class MaterialExportSummary:
    exported: int = 0
    failed: int = 0
    diagnostics: tuple[str, ...] = ()


def _normalise_path(path) -> str:
    if path is None:
        return ""
    return os.fspath(path).replace("\\", "/")


def _path_variants(path) -> tuple[str, ...]:
    raw = _normalise_path(path)
    variants = {raw, os.path.abspath(raw).replace("\\", "/"), os.path.basename(raw)}
    return tuple(sorted(variants))


def _contains_burst_head_token(value) -> bool:
    return bool(_BURST_HEAD_TOKEN.search(_normalise_path(value)))


def _metadata_entry(path, metadata):
    if isinstance(metadata, Mapping):
        for variant in _path_variants(path):
            if variant in metadata:
                return metadata[variant]
        path_variants = _path_variants(path)
        for key, value in metadata.items():
            if _normalise_path(key) in path_variants:
                return value
        for key in ("resources", "by_path", "assets", "items"):
            entries = metadata.get(key)
            if isinstance(entries, Mapping):
                for variant in _path_variants(path):
                    if variant in entries:
                        return entries[variant]
            elif isinstance(entries, (list, tuple)):
                for entry in entries:
                    if isinstance(entry, Mapping) and any(
                        _normalise_path(entry.get(field, "")) in _path_variants(path)
                        for field in ("path", "source_path", "resource_path", "container")
                    ):
                        return entry
    elif isinstance(metadata, (list, tuple)):
        for entry in metadata:
            if isinstance(entry, Mapping) and any(
                _normalise_path(entry.get(field, "")) in _path_variants(path)
                for field in ("path", "source_path", "resource_path", "container")
            ):
                return entry
    return None


def _metadata_confirms_burst_head(path, metadata) -> bool:
    entry = _metadata_entry(path, metadata)
    if entry is None and isinstance(metadata, Mapping):
        keys = {str(key).strip().lower().replace("-", "_") for key in metadata}
        if keys & (_METADATA_KIND_KEYS | {"is_burst_head", "burst_head", "bursthead"}):
            entry = metadata
    if entry is None:
        return False
    if isinstance(entry, Mapping):
        for key, value in entry.items():
            key_text = str(key).strip().lower().replace("-", "_")
            if key_text in {"is_burst_head", "burst_head", "bursthead"} and value is True:
                return True
            if key_text in _METADATA_KIND_KEYS and isinstance(value, str):
                normalised = re.sub(r"[^a-z0-9]+", "-", value.strip().lower()).strip("-")
                if normalised == "burst-head" or _contains_burst_head_token(value):
                    return True
        return False
    if isinstance(entry, str):
        normalised = re.sub(r"[^a-z0-9]+", "-", entry.strip().lower()).strip("-")
        return normalised == "burst-head"
    return False


def is_burst_head_resource(path, metadata=None) -> bool:
    """Return whether a path is explicitly named as a Burst Head resource."""
    return _contains_burst_head_token(path) or _metadata_confirms_burst_head(path, metadata)


def _fingerprint(path: Path) -> str:
    digest = hashlib.sha256()
    try:
        with path.open("rb") as source:
            for chunk in iter(lambda: source.read(1024 * 1024), b""):
                digest.update(chunk)
    except OSError:
        digest.update(f"missing:{_normalise_path(path)}".encode("utf-8"))
    return digest.hexdigest()


def _atlas_package(path: Path) -> str | None:
    lower_name = path.name.lower()
    for suffix in _ATLAS_SUFFIXES:
        if lower_name.endswith(suffix):
            return path.name[: -len(suffix)]
    return None


def _discover_sprite_paths(source_path: Path, package_name: str, files: Iterable[Path]) -> tuple[str, ...]:
    package_prefix = package_name.casefold()
    return tuple(
        str(path)
        for path in files
        if path.parent == source_path.parent
        and path.suffix.lower() in _IMAGE_SUFFIXES
        and path.stem.casefold().startswith(package_prefix)
    )


def discover_game_materials(material_dir, metadata=None) -> GameMaterialCatalog:
    """Discover Burst Head files, FGUI packages, and unclassified files."""
    root = Path(material_dir)
    if not root.is_dir():
        return GameMaterialCatalog((), (), ())

    files = tuple(sorted((path for path in root.rglob("*") if path.is_file()), key=lambda item: _normalise_path(item).casefold()))
    atlas_files: dict[str, list[Path]] = {}
    burst_heads: list[GameMaterialRecord] = []
    unmatched: list[GameMaterialRecord] = []
    for path in files:
        package_name = _atlas_package(path)
        if package_name:
            atlas_files.setdefault(package_name, []).append(path)
            continue
        record = GameMaterialRecord(
            kind="burst-head" if is_burst_head_resource(path, metadata) else "unmatched",
            source_path=str(path),
            display_name=path.stem,
            fingerprint=_fingerprint(path),
        )
        if record.kind == "burst-head":
            burst_heads.append(record)
        else:
            unmatched.append(record)

    atlases = []
    for package_name in sorted(atlas_files, key=str.casefold):
        sources = sorted(
            atlas_files[package_name],
            key=lambda item: (item.suffix.lower() != ".bytes", _normalise_path(item).casefold()),
        )
        source_path = sources[0]
        atlases.append(
            AtlasResourceGroup(
                package_name=package_name,
                source_path=str(source_path),
                sprite_paths=_discover_sprite_paths(source_path, package_name, files),
            )
        )

    return GameMaterialCatalog(tuple(burst_heads), tuple(atlases), tuple(unmatched))


def _safe_filename(value: str) -> str:
    cleaned = re.sub(r"[^A-Za-z0-9._-]+", "_", str(value).strip())
    cleaned = cleaned.strip(" .") or "resource"
    if cleaned.split(".", 1)[0].upper() in {
        "CON", "PRN", "AUX", "NUL", *(f"COM{i}" for i in range(1, 10)), *(f"LPT{i}" for i in range(1, 10))
    }:
        cleaned = f"_{cleaned}"
    return cleaned


def _burst_output_path(record: GameMaterialRecord, output_dir: Path) -> Path:
    source_path = Path(record.source_path)
    suffix = source_path.suffix or ".png"
    candidate = output_dir / f"{_safe_filename(record.display_name or source_path.stem)}{suffix}"
    if not candidate.exists():
        return candidate

    fingerprint = _safe_filename(record.fingerprint[:12] or "source")
    candidate = output_dir / f"{candidate.stem}_{fingerprint}{suffix}"
    if not candidate.exists():
        return candidate

    source_key = hashlib.sha256(_normalise_path(source_path).encode("utf-8")).hexdigest()[:12]
    index = 2
    while True:
        candidate = output_dir / f"{candidate.stem}_{source_key}_{index}{suffix}"
        if not candidate.exists():
            return candidate
        index += 1


def export_game_materials(catalog: GameMaterialCatalog, output_dir, splitter) -> MaterialExportSummary:
    """Export known game materials while retaining existing output on failures."""
    root = Path(output_dir)
    burst_output = root / "game_material" / "burst-head"
    fgui_output = root / "fgui"
    exported = 0
    failed = 0
    diagnostics: list[str] = []

    for record in catalog.burst_heads:
        if record.kind != "burst-head":
            continue
        source_path = Path(record.source_path)
        if not source_path.is_file():
            failed += 1
            diagnostics.append(f"burst-head source missing: {record.source_path}")
            continue
        try:
            burst_output.mkdir(parents=True, exist_ok=True)
            shutil.copy2(source_path, _burst_output_path(record, burst_output))
            exported += 1
        except OSError as error:
            failed += 1
            diagnostics.append(f"burst-head export failed for {record.source_path}: {error}")

    for group in catalog.atlases:
        source_path = Path(group.source_path)
        destination = fgui_output / _safe_filename(group.package_name)
        if not source_path.is_file():
            failed += 1
            diagnostics.append(f"atlas source missing: {group.source_path}")
            continue
        try:
            destination.mkdir(parents=True, exist_ok=True)
            result = splitter(str(source_path), str(destination), False)
            if result is False:
                raise RuntimeError("splitter returned failure")
            exported += 1
        except Exception as error:
            failed += 1
            diagnostics.append(f"atlas export failed for {group.package_name}: {error}")

    return MaterialExportSummary(exported=exported, failed=failed, diagnostics=tuple(diagnostics))
