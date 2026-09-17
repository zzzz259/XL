"""Identity-based Spine skin export plans."""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable

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
    scale: int = 4
    max_resolution: int = 8192
    margin: int = 0
    transparent: bool = True
    pma: bool = True
    format: str = "Png"
    fps: int = 1


@dataclass(frozen=True, slots=True)
class SkinExportJob:
    """One output file and the complete resource identity that produces it."""

    record: SpineSkinRecord
    output_path: Path
    settings: ExportSettings


def _safe_component(value: str, fallback: str) -> str:
    value = re.sub(r"[\\/:*?\"<>|]", "_", str(value)).strip(" .")
    if value and value.split(".", 1)[0].casefold() in _WINDOWS_RESERVED_DEVICE_NAMES:
        value = f"_{value}"
    return value or fallback


def _validate_png_settings(settings: ExportSettings) -> None:
    if str(settings.format).casefold() != "png":
        raise ValueError("Skin export jobs only support PNG format")


def build_export_plan(
    records: Iterable[SpineSkinRecord], settings: ExportSettings, output_dir
) -> tuple[SkinExportJob, ...]:
    """Build one PNG job per ready, character-matched Spine skin."""
    _validate_png_settings(settings)
    root = Path(output_dir)
    jobs = []
    for record in records:
        if not record.character_id or record.status != "ready" or not record.skin_name:
            continue
        character_dir = _safe_component(record.character_id, "unmatched")
        skin_dir = root / character_dir / skin_key(record)
        filename = f"{_safe_component(record.skin_name, 'skin')}.png"
        jobs.append(SkinExportJob(record, skin_dir / filename, settings))
    return tuple(jobs)


def build_spine_export_command(job: SkinExportJob, spine_cli: str) -> list[str]:
    """Return the CLI command for ``job`` without inferring identity from paths."""
    from .spine_adapter import build_spine_export_command as _build_command

    return _build_command(job, spine_cli)
