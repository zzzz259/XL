"""角色图鉴渲染使用的稳定数据边界。"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

NUMERIC_FIELDS = {
    "init_atk",
    "init_def",
    "init_hp",
    "max_atk",
    "max_def",
    "max_hp",
    "crt",
    "blk",
    "crt_int",
    "blk_int",
    "spd_move",
    "spd_atk",
    "range_atk",
    "weight",
}
TEXT_FIELDS = {
    "name",
    "star",
    "profession",
    "element",
    "birthday",
    "height",
    "faction",
    "cv",
    "description",
    "leader_skill",
    "normal_skill",
    "special_skill",
    "burst_skill",
    "badge_info",
}
LIST_FIELDS = {
    "breakthrough_costs",
    "normal_skill_upgrade_costs",
    "passive_skill_upgrade_costs",
}


@dataclass(frozen=True)
class CharacterCardRecord:
    """One character record ready for long-image rendering."""

    character_id: str
    data: dict[str, Any]


def normalize_character_record(character_id: str | int, data: Mapping[str, Any] | None) -> CharacterCardRecord:
    """Copy one parsed character record and fill renderer-safe defaults."""

    values = dict(data or {})
    for key in NUMERIC_FIELDS:
        if values.get(key) is None:
            values[key] = 0
    for key in TEXT_FIELDS:
        if values.get(key) is None:
            values[key] = "未知"
    for key in LIST_FIELDS:
        if values.get(key) is None:
            values[key] = []
    return CharacterCardRecord(str(character_id), values)


def _sort_key(record: CharacterCardRecord) -> tuple[int, int | str]:
    if record.character_id.isdecimal():
        return (0, int(record.character_id))
    return (1, record.character_id)


def character_card_records(characters: Mapping[str | int, Mapping[str, Any]]) -> tuple[CharacterCardRecord, ...]:
    """Normalize and stably sort the parsed character mapping."""

    records = (normalize_character_record(character_id, data) for character_id, data in characters.items())
    return tuple(sorted(records, key=_sort_key))
