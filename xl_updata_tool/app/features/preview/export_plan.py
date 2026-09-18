"""Identity-based Spine skin export plans."""

from __future__ import annotations

import re
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Iterable

from .character_names import CharacterNameResolver, display_resource_family, display_skin_number
from .resource_model import SpineSkinRecord, skin_key


_WINDOWS_RESERVED_DEVICE_NAMES = {
    "con",
    "prn",
    "aux",
    "nul",
    *(f"com{index}" for index in range(1, 10)),
    *(f"lpt{index}" for index in range(1, 10)),
}


@dataclass(frozen=True, slots=True)
class ExportSettings:
    """The user-selected settings for one skin export batch."""

    animation: str = "idle"
    static: bool = True
    scale: int = 4
    max_resolution: int = 8192
    margin: int = 0
    transparent: bool = True
    pma: bool = True
    format: str = "Png"
    fps: int = 1
    physics: str = "Update"
    warm_up: float = 0.0
    time_offset: float = 0.0
    duration: float | None = 2.0
    speed: float = 1.0
    loop: bool = False
    disable_track_loop: bool = False
    skins: tuple[str, ...] = ("default",)
    background_color: str = "#00000000"
    auto_border: bool = True


@dataclass(frozen=True, slots=True)
class SkinExportJob:
    """One output file and the complete resource identity that produces it."""

    record: SpineSkinRecord
    output_path: Path
    settings: ExportSettings
    records: tuple[SpineSkinRecord, ...] = ()
    export_mode: str = "custom"

    def __post_init__(self):
        if not self.records:
            object.__setattr__(self, "records", (self.record,))


def _safe_component(value: str, fallback: str) -> str:
    value = re.sub(r"[\\/:*?\"<>|]", "_", str(value)).strip(" .")
    if value and value.split(".", 1)[0].casefold() in _WINDOWS_RESERVED_DEVICE_NAMES:
        value = f"_{value}"
    return value or fallback


def _safe_filename_stem(value: str, fallback: str) -> str:
    """Sanitize a filename stem without applying reserved-name rules to a compound name."""
    value = re.sub(r"[\\/:*?\"<>|]", "_", str(value)).strip(" .")
    return value or fallback


def _validate_settings(settings: ExportSettings) -> None:
    if str(settings.format).casefold() not in {"png", "mp4", "gif"}:
        raise ValueError("Skin export jobs support PNG, MP4, or GIF format")


def _normalise_groups(records: Iterable[SpineSkinRecord | Iterable[SpineSkinRecord]]):
    for value in records:
        if isinstance(value, SpineSkinRecord):
            yield (value,)
        else:
            group = tuple(value)
            if group:
                yield group


def _primary_record(records: Iterable[SpineSkinRecord]) -> SpineSkinRecord:
    return min(
        tuple(records),
        key=lambda record: (
            "_bg" in record.source_skel.casefold(),
            record.skin_name.casefold() != "default",
            not bool(record.atlas_path),
            record.source_skel.casefold(),
        ),
    )


def build_export_plan(
    records: Iterable[SpineSkinRecord],
    settings: ExportSettings,
    output_dir,
    name_resolver: CharacterNameResolver | None = None,
) -> tuple[SkinExportJob, ...]:
    """Build one job per visible skin, retaining all source parts."""
    _validate_settings(settings)
    root = Path(output_dir)
    jobs = []
    used_paths: set[Path] = set()
    for source_group in _normalise_groups(records):
        record = _primary_record(source_group)
        if (
            (not record.character_id and record.resource_family != "eventcovers")
            or not any(item.status == "ready" and item.atlas_path for item in source_group)
            or not record.skin_name
        ):
            continue
        raw_character_id = str(record.character_id or "eventcovers").strip() or "eventcovers"
        character_id = _safe_component(raw_character_id, "eventcovers")
        character_name = name_resolver.resolve(record.character_id) if name_resolver else None
        role_dir_name = character_id
        if character_name:
            role_dir_name = _safe_component(f"{character_id}_{character_name}", character_id)
        role_dir = root / role_dir_name
        skin_number = _safe_component(
            display_skin_number(record.source_skel, record.character_id, record.resource_family),
            "skin",
        )
        family_label = _safe_filename_stem(
            display_resource_family(record.resource_family),
            "Spine",
        )
        filename_id = _safe_filename_stem(raw_character_id, "eventcovers")
        extension = str(settings.format).casefold()
        candidate = role_dir / f"{filename_id}_{family_label}_{skin_number}.{extension}"
        if candidate in used_paths:
            candidate = role_dir / f"{filename_id}_{family_label}_{skin_number}_{skin_key(record)[:8]}.{extension}"
        used_paths.add(candidate)
        part_groups: dict[tuple[str, str], list[SpineSkinRecord]] = {}
        for item in source_group:
            if item.status != "ready" or not item.atlas_path:
                continue
            source_key = (item.source_skel.casefold(), item.atlas_path.casefold())
            part_groups.setdefault(source_key, []).append(item)
        parts = tuple(_primary_record(items) for items in part_groups.values())
        jobs.append(SkinExportJob(record, candidate, settings, records=parts))
    return tuple(jobs)


def build_default_export_plan(
    records: Iterable[SpineSkinRecord | Iterable[SpineSkinRecord]],
    character_output_dir,
    name_resolver: CharacterNameResolver | None = None,
    animation_duration=None,
    video_output_dir=None,
) -> tuple[SkinExportJob, ...]:
    """Expand checked visible skins according to the builtin output matrix."""
    from .export_presets import default_export_settings

    character_root = Path(character_output_dir)
    video_root = Path(video_output_dir) if video_output_dir is not None else character_root
    jobs: list[SkinExportJob] = []
    for source_group in _normalise_groups(records):
        ready = tuple(item for item in source_group if item.status == "ready" and item.atlas_path)
        if not ready:
            continue
        family = str(ready[0].resource_family or "spine").casefold()
        output_specs = [("Png", character_root, animation_duration)]
        if family == "cardspine":
            output_specs.append(("Mp4", video_root, animation_duration))
        for output_format, output_root, duration in output_specs:
            settings = default_export_settings(family, output_format, duration)
            jobs.extend(
                replace(job, export_mode="builtin")
                for job in build_export_plan(
                    (source_group,), settings, output_root, name_resolver
                )
            )
    return tuple(jobs)


def build_spine_export_command(job: SkinExportJob, spine_cli: str) -> list[str]:
    """Return the CLI command for ``job`` without inferring identity from paths."""
    from .spine_adapter import build_spine_export_command as _build_command

    return _build_command(job, spine_cli)
