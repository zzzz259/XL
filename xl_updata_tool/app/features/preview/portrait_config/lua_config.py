"""Narrow readers for version-scoped Lua portrait configuration."""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True, slots=True)
class PortraitRoot:
    skin_id: str
    version_timestamp: int
    prefab_name: str | None
    position: tuple[float, ...] | None
    metadata: tuple[tuple[str, str], ...]
    evidence: str
    field_evidence: tuple[tuple[str, str], ...] = ()
    unresolved_reason: str | None = None


_ENTRY_START = re.compile(r"\[\s*(\d+)\s*\]\s*=\s*\{")
_STRING = re.compile(r"\bshow_spine\s*=\s*['\"]([^'\"]+)['\"]")
_POSITION = re.compile(r"\bshow_spine_position\s*=\s*\{([^}]*)\}")
_NUMBER = re.compile(r"[-+]?(?:\d+\.?\d*|\.\d+)")
_CARD_ID = re.compile(r"\bcard_id\s*=\s*(\d+)")
_ENTRY_ID = re.compile(r"\bid\s*=\s*(\d+)")
_BG_MAIN = re.compile(r"\bbg_main\s*=\s*(['\"])(.*?)\1")
_EVENTCOVER_PREFAB = re.compile(r"(?i)(?:^|[/\\])eventcovers_(\d{4})\.prefab$")
_LONG_COMMENT = re.compile(r"--\[(=*)\[")


def _mask_lua_comments(text: str) -> str:
    """Replace Lua comments with spaces while preserving offsets and line numbers."""
    masked = list(text)
    index = 0
    quote: str | None = None
    while index < len(text):
        char = text[index]
        if quote:
            if char == "\\":
                index += 2
                continue
            if char == quote:
                quote = None
            index += 1
            continue
        if char in "'\"":
            quote = char
            index += 1
            continue
        if text.startswith("--", index):
            long_comment = _LONG_COMMENT.match(text, index)
            if long_comment:
                closing = "]" + long_comment.group(1) + "]"
                end = text.find(closing, long_comment.end())
                end = len(text) if end < 0 else end + len(closing)
            else:
                newline = text.find("\n", index + 2)
                end = len(text) if newline < 0 else newline
            for offset in range(index, end):
                if masked[offset] not in "\r\n":
                    masked[offset] = " "
            index = end
            continue
        index += 1
    return "".join(masked)


def _matching_table_end(text: str, opening_brace: int) -> int | None:
    """Find a Lua table's closing brace without counting braces in strings/comments."""
    depth = 0
    index = opening_brace
    quote: str | None = None
    while index < len(text):
        char = text[index]
        if quote:
            if char == "\\":
                index += 2
                continue
            if char == quote:
                quote = None
            index += 1
            continue
        if text.startswith("--[[", index):
            comment_end = text.find("]]", index + 4)
            if comment_end < 0:
                return None
            index = comment_end + 2
            continue
        if text.startswith("--", index):
            newline = text.find("\n", index + 2)
            if newline < 0:
                return None
            index = newline + 1
            continue
        if char in "'\"":
            quote = char
        elif char == "{":
            depth += 1
        elif char == "}":
            depth -= 1
            if depth == 0:
                return index
        index += 1
    return None


def _field_evidence(source: Path, content: str, body_start: int, body: str, pattern: re.Pattern, field: str):
    match = pattern.search(body)
    if not match:
        return None
    absolute_offset = body_start + match.start()
    line = content.count("\n", 0, absolute_offset) + 1
    return field, f"{source}:{line}"


def load_portrait_roots(lua_root: Path, version_timestamp: int) -> list[PortraitRoot]:
    """Read BaseFashion entries from exactly one version directory.

    Missing config references remain unresolved; asset filenames are never used
    to infer a root.
    """
    source = Path(lua_root) / str(int(version_timestamp)) / "BaseFashion.lua"
    try:
        content = source.read_text(encoding="utf-8-sig", errors="replace")
    except OSError:
        return []
    searchable = _mask_lua_comments(content)
    roots: list[PortraitRoot] = []
    cursor = 0
    while match := _ENTRY_START.search(searchable, cursor):
        opening_brace = match.end() - 1
        closing_brace = _matching_table_end(searchable, opening_brace)
        if closing_brace is None:
            break
        body = searchable[match.end():closing_brace]
        prefab_match = _STRING.search(body)
        position_match = _POSITION.search(body)
        position = None
        if position_match:
            values = _NUMBER.findall(position_match.group(1))
            if len(values) >= 2:
                position = tuple(float(value) for value in values)
        card_match = _CARD_ID.search(body)
        id_match = _ENTRY_ID.search(body)
        metadata = tuple((key, match.group(1)) for key, match in (
            ("card_id", card_match), ("entry_id", id_match),
        ) if match)
        field_evidence = tuple(item for item in (
            _field_evidence(source, content, match.end(), body, _STRING, "show_spine"),
            _field_evidence(source, content, match.end(), body, _POSITION, "show_spine_position"),
            _field_evidence(source, content, match.end(), body, _CARD_ID, "card_id"),
            _field_evidence(source, content, match.end(), body, _ENTRY_ID, "id"),
        ) if item)
        entry_line = content.count("\n", 0, match.start()) + 1
        roots.append(PortraitRoot(
            skin_id=match.group(1), version_timestamp=int(version_timestamp),
            prefab_name=prefab_match.group(1) if prefab_match else None,
            position=position, metadata=metadata,
            evidence=f"{source}:{entry_line}", field_evidence=field_evidence,
            unresolved_reason=None if prefab_match else "missing_show_spine",
        ))
        cursor = closing_brace + 1
    return roots


def load_eventcover_roots(lua_root: Path, version_timestamp: int) -> list[PortraitRoot]:
    """Read concurrent EventCover scenes from configured ``bg_main`` roots.

    Entrance animation ``PATH_LOOKUP`` values are deliberately not scanned:
    those resources are spawned and disposed sequentially, while this catalog
    describes the cover Prefab that the game mounts as one background scene.
    """
    source = Path(lua_root) / str(int(version_timestamp)) / "BaseActivityStageConfig.lua"
    try:
        content = source.read_text(encoding="utf-8-sig", errors="replace")
    except OSError:
        return []
    searchable = _mask_lua_comments(content)
    roots_by_event: dict[str, PortraitRoot] = {}
    cursor = 0
    while match := _ENTRY_START.search(searchable, cursor):
        opening_brace = match.end() - 1
        closing_brace = _matching_table_end(searchable, opening_brace)
        if closing_brace is None:
            break
        body = searchable[match.end():closing_brace]
        bg_match = _BG_MAIN.search(body)
        if bg_match:
            prefab_path = bg_match.group(2).strip().replace("\\", "/")
            prefab_match = _EVENTCOVER_PREFAB.search(prefab_path)
            if prefab_match:
                event_id = prefab_match.group(1)
                entry_match = _ENTRY_ID.search(body)
                entry_id = entry_match.group(1) if entry_match else match.group(1)
                field_evidence = _field_evidence(
                    source, content, match.end(), body, _BG_MAIN, "bg_main",
                )
                root = PortraitRoot(
                    skin_id=f"eventcovers_{event_id}",
                    version_timestamp=int(version_timestamp),
                    prefab_name=prefab_path,
                    position=None,
                    metadata=(("entry_id", entry_id), ("eventcover_id", event_id)),
                    evidence=f"{source}:{content.count(chr(10), 0, match.start()) + 1}",
                    field_evidence=(field_evidence,) if field_evidence else (),
                )
                previous = roots_by_event.get(event_id)
                if previous is None:
                    roots_by_event[event_id] = root
                elif previous.prefab_name != root.prefab_name:
                    roots_by_event[event_id] = PortraitRoot(
                        skin_id=root.skin_id,
                        version_timestamp=int(version_timestamp),
                        prefab_name=None,
                        position=None,
                        metadata=(
                            ("eventcover_id", event_id),
                            ("conflicting_roots", ";".join(sorted({
                                str(previous.prefab_name or ""), str(root.prefab_name or ""),
                            }))),
                        ),
                        evidence=f"{previous.evidence};{root.evidence}",
                        field_evidence=tuple(dict.fromkeys((
                            *previous.field_evidence, *root.field_evidence,
                        ))),
                        unresolved_reason="ambiguous_eventcover_root",
                    )
        cursor = closing_brace + 1
    return list(roots_by_event.values())
