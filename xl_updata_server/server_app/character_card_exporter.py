"""Batch export of parsed characters into archive long images."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .character_card_renderer import render_character_cards_batch
from .character_cards import CharacterCardRecord, character_card_records
from .extractor import safe_role_name


@dataclass(frozen=True)
class CardExportFailure:
    character_id: str
    error: str


@dataclass(frozen=True)
class CardExportReport:
    seen: int
    created: int
    warned: int
    failed: int
    outputs: tuple[Path, ...]
    warnings: tuple[str, ...]
    failures: tuple[CardExportFailure, ...]
    character_outputs: tuple[tuple[str, Path], ...] = ()


def _output_path(record: CharacterCardRecord, output_dir: Path) -> Path:
    name = safe_role_name(str(record.data.get("name", "未知")).split("/", 1)[0])
    return output_dir / f"{safe_role_name(record.character_id)}_{name}_角色档案_长图.png"


def export_character_cards(
    characters: dict[str | int, dict[str, Any]],
    output_dir: str | Path,
    font_path: str | Path | None = None,
    only_character_ids: set[str] | None = None,
    node_bin: str = "node",
    renderer_dir: str | Path | None = None,
) -> CardExportReport:
    """Render characters in one Node batch, optionally only newly added ones.

    font_path 已弃用：字体资源改由 renderer/assets/fonts/ 管理。
    """

    destination = Path(output_dir)
    destination.mkdir(parents=True, exist_ok=True)
    if only_character_ids is not None:
        wanted = {str(item) for item in only_character_ids}
        characters = {cid: data for cid, data in characters.items() if str(cid) in wanted}
    records = character_card_records(characters)

    outputs: list[Path] = []
    character_outputs: list[tuple[str, Path]] = []
    warnings: list[str] = []
    failures: list[CardExportFailure] = []
    warned = 0

    try:
        render_results = render_character_cards_batch(
            records,
            out_dir=destination,
            node_bin=node_bin,
            renderer_dir=renderer_dir,
        )
    except Exception as error:  # pragma: no cover - batch-level failures are catastrophic
        return CardExportReport(
            seen=len(records),
            created=0,
            warned=0,
            failed=len(records),
            outputs=(),
            warnings=(),
            failures=tuple(CardExportFailure(record.character_id, str(error)) for record in records),
        )

    for result in render_results:
        if result.error:
            failures.append(CardExportFailure(result.character_id, result.error))
            continue
        outputs.append(result.output_path)
        character_outputs.append((result.character_id, result.output_path))
        if result.warnings:
            warned += 1
            warnings.extend(f"{result.character_id}: {item}" for item in result.warnings)

    return CardExportReport(
        seen=len(records),
        created=len(outputs),
        warned=warned,
        failed=len(failures),
        outputs=tuple(outputs),
        warnings=tuple(warnings),
        failures=tuple(failures),
        character_outputs=tuple(character_outputs),
    )
