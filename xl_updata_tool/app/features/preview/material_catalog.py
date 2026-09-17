"""Game-material discovery and export for the preview workbench."""

from __future__ import annotations

import hashlib
import json
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
_BURST_MANIFEST_NAME = ".burst-head-manifest.json"
_BURST_SIDECAR_SUFFIX = ".burst-head.json"
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
        path_variants = _path_variants(path)
        for key in ("resources", "by_path", "assets", "items"):
            entries = metadata.get(key)
            if isinstance(entries, Mapping):
                for variant in _path_variants(path):
                    if variant in entries:
                        return entries[variant]
                path_variants = _path_variants(path)
                for entry_path, entry in entries.items():
                    if _normalise_path(entry_path) in path_variants:
                        return entry
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


def _source_key(path: Path) -> str:
    return os.path.normcase(os.path.abspath(os.fspath(path))).replace("\\", "/")


class _BurstManifestError(ValueError):
    """Raised when the persisted Burst Head manifest cannot be trusted."""


def _normalise_manifest_entry(source, entry) -> dict[str, str]:
    if not isinstance(source, str) or not source:
        raise _BurstManifestError("source key must be a non-empty string")
    if not isinstance(entry, Mapping):
        raise _BurstManifestError(f"entry for {source!r} must be an object")
    fingerprint = entry.get("fingerprint")
    output_name = entry.get("output")
    if not isinstance(fingerprint, str) or not fingerprint:
        raise _BurstManifestError(f"entry for {source!r} has an invalid fingerprint")
    if not isinstance(output_name, str) or not output_name or Path(output_name).name != output_name:
        raise _BurstManifestError(f"entry for {source!r} has an invalid output")
    return {"fingerprint": fingerprint, "output": output_name}


def _load_burst_manifest(output_dir: Path) -> dict[str, Mapping[str, str]]:
    manifest_path = output_dir / _BURST_MANIFEST_NAME
    if not manifest_path.exists():
        return {}
    try:
        payload = json.loads(manifest_path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as error:
        raise _BurstManifestError(str(error)) from error
    if not isinstance(payload, Mapping) or payload.get("version") != 1:
        raise _BurstManifestError("manifest root must contain version 1")
    entries = payload.get("entries")
    if not isinstance(entries, Mapping):
        raise _BurstManifestError("manifest entries must be an object")
    return {
        source: _normalise_manifest_entry(source, entry)
        for source, entry in entries.items()
    }


def _save_burst_sidecar(target: Path, source: Path, fingerprint: str) -> None:
    sidecar_path = target.with_name(f"{target.name}{_BURST_SIDECAR_SUFFIX}")
    temporary_path = sidecar_path.with_name(f"{sidecar_path.name}.tmp")
    try:
        temporary_path.write_text(
            json.dumps(
                {"version": 1, "source": _source_key(source), "fingerprint": fingerprint, "output": target.name},
                ensure_ascii=False,
                indent=2,
                sort_keys=True,
            ),
            encoding="utf-8",
        )
        os.replace(temporary_path, sidecar_path)
    except Exception:
        try:
            temporary_path.unlink()
        except OSError:
            pass
        raise


def _load_burst_sidecars(output_dir: Path) -> dict[str, Mapping[str, str]]:
    sidecars: dict[str, Mapping[str, str]] = {}
    for sidecar_path in output_dir.glob(f"*{_BURST_SIDECAR_SUFFIX}"):
        try:
            payload = json.loads(sidecar_path.read_text(encoding="utf-8"))
            if not isinstance(payload, Mapping) or payload.get("version") != 1:
                continue
            source = payload.get("source")
            entry = _normalise_manifest_entry(source, payload)
            if (output_dir / entry["output"]).is_file() and sidecar_path.name == f"{entry['output']}{_BURST_SIDECAR_SUFFIX}":
                sidecars[source] = entry
        except (OSError, UnicodeError, json.JSONDecodeError, _BurstManifestError):
            continue
    return sidecars


def _save_burst_manifest(output_dir: Path, entries: Mapping[str, Mapping[str, str]]) -> None:
    manifest_path = output_dir / _BURST_MANIFEST_NAME
    temporary_path = manifest_path.with_name(f"{manifest_path.name}.tmp")
    try:
        temporary_path.write_text(
            json.dumps({"version": 1, "entries": entries}, ensure_ascii=False, indent=2, sort_keys=True),
            encoding="utf-8",
        )
        os.replace(temporary_path, manifest_path)
    except Exception:
        try:
            temporary_path.unlink()
        except OSError:
            pass
        raise


def _manifest_target(record: GameMaterialRecord, output_dir: Path, manifest) -> Path | None:
    entry = manifest.get(_source_key(Path(record.source_path)))
    if not isinstance(entry, Mapping) or entry.get("fingerprint") != record.fingerprint:
        return None
    output_name = entry.get("output")
    if not isinstance(output_name, str) or Path(output_name).name != output_name:
        return None
    target = output_dir / output_name
    return target if target.is_file() else None


def _burst_output_path(record: GameMaterialRecord, output_dir: Path, manifest) -> Path:
    existing_target = _manifest_target(record, output_dir, manifest)
    if existing_target is not None:
        return existing_target

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


def _copy_burst_head(record: GameMaterialRecord, source_path: Path, target: Path) -> None:
    target_existed = target.exists()
    try:
        shutil.copy2(source_path, target)
        _save_burst_sidecar(target, source_path, record.fingerprint)
    except Exception:
        if not target_existed:
            try:
                target.unlink()
            except OSError:
                pass
        raise


def export_game_materials(catalog: GameMaterialCatalog, output_dir, splitter) -> MaterialExportSummary:
    """Export known game materials while retaining existing output on failures."""
    root = Path(output_dir)
    burst_output = root / "game_material" / "burst-head"
    fgui_output = root / "fgui"
    exported = 0
    failed = 0
    diagnostics: list[str] = []
    try:
        burst_manifest = _load_burst_manifest(burst_output)
    except _BurstManifestError as error:
        burst_manifest = {}
        failed += 1
        diagnostics.append(f"burst-head manifest load failed: {error}")
    burst_manifest_changed = False
    for source, entry in _load_burst_sidecars(burst_output).items():
        if source not in burst_manifest:
            burst_manifest[source] = entry
            burst_manifest_changed = True

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
            target = _burst_output_path(record, burst_output, burst_manifest)
            existing_target = _manifest_target(record, burst_output, burst_manifest)
            if existing_target is None:
                _copy_burst_head(record, source_path, target)
                burst_manifest[_source_key(source_path)] = {
                    "fingerprint": record.fingerprint,
                    "output": target.name,
                }
                burst_manifest_changed = True
            elif burst_manifest.get(_source_key(source_path), {}).get("output") != existing_target.name:
                burst_manifest[_source_key(source_path)] = {
                    "fingerprint": record.fingerprint,
                    "output": existing_target.name,
                }
                burst_manifest_changed = True
            exported += 1
        except Exception as error:
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

    if burst_manifest_changed:
        try:
            _save_burst_manifest(burst_output, burst_manifest)
        except Exception as error:
            failed += 1
            diagnostics.append(f"burst-head manifest save failed: {error}")

    return MaterialExportSummary(exported=exported, failed=failed, diagnostics=tuple(diagnostics))
