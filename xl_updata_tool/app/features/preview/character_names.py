"""Stable, Qt-free labels for preview character and skin resources."""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .resource_model import spine_source_stem


_FAMILY_LABELS = {
    "cardspine": "角色立绘",
    "battlespine": "战斗小人",
    "eventcovers": "活动封面",
    "spine": "Spine",
}
_FAMILY_PREFIXES = ("cardspine", "battlespine", "eventcovers")


def _name_only(value: Any) -> str | None:
    if isinstance(value, dict):
        value = value.get("name")
    if value is None:
        return None
    value = str(value).strip()
    if not value:
        return None
    return value.split("/", 1)[0].strip() or None


def _characters_from_payload(payload: Any) -> dict[str, Any]:
    if not isinstance(payload, dict):
        return {}
    characters = payload.get("current_characters")
    if not isinstance(characters, dict):
        characters = payload.get("characters_full")
    if not isinstance(characters, dict):
        return {}
    return {str(key): value for key, value in characters.items()}


def _load_json(path: Path) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, ValueError, TypeError):
        return None


def _spine_card_aliases(source_dir: Path) -> dict[str, str]:
    """Read the reliable Spine-id -> card-id relation from BaseFashion.lua."""
    path = source_dir / "BaseFashion.lua"
    try:
        text = path.read_text(encoding="utf-8", errors="replace")
    except (OSError, UnicodeError):
        return {}
    aliases: dict[str, str] = {}
    for block in re.split(r"\n\s*\},", text):
        card_match = re.search(r"\bcard_id\s*=\s*(\d+)", block)
        if not card_match:
            continue
        card_id = card_match.group(1)
        for spine_id in re.findall(r"(?:cardspine|battlespine)_(\d+)", block, flags=re.IGNORECASE):
            aliases.setdefault(spine_id, card_id)
    return aliases


@dataclass(frozen=True, slots=True)
class CharacterNameResolver:
    """Resolve character IDs from current data, then historical snapshots."""

    _names: dict[str, str]

    @classmethod
    def from_output_root(cls, output_root) -> "CharacterNameResolver":
        root = Path(output_root)
        repository_path = root / "character_data" / "characters_repository.json"
        repository = _load_json(repository_path)
        names: dict[str, str] = {}
        if not isinstance(repository, dict):
            return cls(names)

        for character_id, value in _characters_from_payload(repository).items():
            name = _name_only(value)
            if name:
                names[character_id] = name

        history = repository.get("history")
        if not isinstance(history, dict):
            history = {}
        source_dirs: list[Path] = []
        current_version = repository.get("current_version")
        if current_version is not None:
            source_dirs.append(root / "lua" / str(current_version))
        for entry in history.values():
            if not isinstance(entry, dict):
                continue
            source_dir = entry.get("source_dir")
            if source_dir:
                source_dirs.append(Path(str(source_dir)))
            snapshot = entry.get("snapshot")
            if not snapshot:
                continue
            payload = _load_json(Path(str(snapshot)))
            for character_id, value in _characters_from_payload(payload).items():
                if character_id in names:
                    continue
                name = _name_only(value)
                if name:
                    names[character_id] = name

        versions_dir = root / "character_data" / "versions"
        if versions_dir.is_dir():
            for snapshot in sorted(versions_dir.glob("*.json"), reverse=True):
                payload = _load_json(snapshot)
                for character_id, value in _characters_from_payload(payload).items():
                    if character_id in names:
                        continue
                    name = _name_only(value)
                    if name:
                        names[character_id] = name
        for source_dir in source_dirs:
            for spine_id, card_id in _spine_card_aliases(source_dir).items():
                if spine_id not in names and card_id in names:
                    names[spine_id] = names[card_id]
        return cls(names)

    def resolve(self, character_id) -> str | None:
        return self._names.get(str(character_id).strip()) if character_id is not None else None


def display_resource_family(resource_family: str) -> str:
    """Return the user-facing label for a resource family."""
    value = str(resource_family or "spine").strip().casefold()
    return _FAMILY_LABELS.get(value, value or "Spine")


def display_skin_number(skel_path, character_id, resource_family) -> str:
    """Extract a stable skin number/name from the source file, not Spine internals."""
    stem = spine_source_stem(str(skel_path))
    if stem.casefold().endswith("_bg"):
        stem = stem[:-3]

    family = str(resource_family or "").casefold()
    character = str(character_id or "").strip()
    prefix = f"{family}_{character}_" if family in _FAMILY_PREFIXES and character else ""
    if prefix and stem.casefold().startswith(prefix.casefold()):
        suffix = stem[len(prefix):].strip("_-")
        if suffix:
            match = re.match(r"(\d+)(?:$|[_-])", suffix)
            return match.group(1) if match else suffix

    base_prefix = f"{family}_{character}" if family in _FAMILY_PREFIXES and character else ""
    if base_prefix and stem.casefold() == base_prefix.casefold():
        # In the game's BattleSpine naming, the unsuffixed model is the
        # logical variant 2; variant 1/3/4 are explicitly suffixed.  Treating
        # it as a generic "基础" skin makes battle-2 indistinguishable from
        # an unnamed resource.
        if family == "battlespine":
            return "2"
        return "基础"

    for prefix in _FAMILY_PREFIXES:
        marker = f"{prefix}_"
        if stem.casefold().startswith(marker):
            suffix = stem[len(marker):].strip("_-")
            if suffix:
                return suffix
    return stem or "skin"


def display_skin_label(skel_path, character_id, resource_family) -> str:
    """Return the user-facing resource-family + source-number label."""
    family = display_resource_family(resource_family)
    number = display_skin_number(skel_path, character_id, resource_family)
    return f"{family} {number or '基础'}"


def display_role_label(character_id, resource_family, resolver: CharacterNameResolver | None) -> str:
    identifier = str(character_id or "未匹配").strip() or "未匹配"
    name = resolver.resolve(identifier) if resolver else None
    family = display_resource_family(resource_family)
    return f"{identifier} · {name} · {family}" if name else f"{identifier} · {family}"
