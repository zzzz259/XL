"""Safely read the data tables needed for gacha history from Lua source."""

from __future__ import annotations

import ast
import re
from typing import Any


_TOKEN = re.compile(
    r"(?P<space>\s+)|"
    r"(?P<comment>--\[(?P<comment_equals>=*)\[.*?\](?P=comment_equals)\]|--[^\r\n]*)|"
    r"(?P<longstring>\[(?P<long_equals>=*)\[.*?\](?P=long_equals)\])|"
    r"(?P<string>\"(?:\\.|[^\"\\])*\"|'(?:\\.|[^'\\])*')|"
    r"(?P<identifier>[A-Za-z_][A-Za-z0-9_]*)|"
    r"(?P<number>0[xX][0-9A-Fa-f]+|\d+(?:\.\d+)?)|"
    r"(?P<symbol>\.\.|==|~=|<=|>=|[{}\[\]=,;().:+*/%-])|(?P<other>.)",
    re.DOTALL,
)


def _tokens(source: str) -> list[str]:
    result: list[str] = []
    position = 0
    while position < len(source):
        match = _TOKEN.match(source, position)
        if match is None:
            raise ValueError(f"unsupported Lua syntax at offset {position}")
        position = match.end()
        if match.group("space") is not None or match.group("comment") is not None:
            continue
        token = match.group()
        if match.group("longstring") is not None:
            delimiter_width = len(match.group("long_equals")) + 2
            result.append(repr(token[delimiter_width:-delimiter_width]))
        else:
            result.append(token)
    return result


class _TableParser:
    def __init__(self, tokens: list[str]):
        self.tokens = tokens
        self.index = 0

    def peek(self, offset: int = 0) -> str | None:
        index = self.index + offset
        return self.tokens[index] if index < len(self.tokens) else None

    def take(self) -> str:
        token = self.peek()
        if token is None:
            raise ValueError("unterminated Lua table")
        self.index += 1
        return token

    def _read_key(self) -> Any:
        self.take()  # [
        token = self.take()
        if token.startswith(("'", '"')):
            key = ast.literal_eval(token)
        elif re.fullmatch(r"(?:0[xX][0-9A-Fa-f]+|\d+)", token):
            key = int(token, 16) if token.lower().startswith("0x") else int(token, 10)
        else:
            key = token
        if self.take() != "]":
            raise ValueError("malformed Lua table key")
        if self.take() != "=":
            raise ValueError("Lua table key is missing '='")
        return key

    def _function_value(self) -> Any:
        start = self.index
        depth = 0
        translation = None
        while (token := self.peek()) is not None:
            token = self.take()
            if token == "function":
                depth += 1
            elif token == "end":
                depth -= 1
                if depth == 0:
                    break
            if token == "T" and self.peek() == "(":
                save = self.index
                self.take()
                arg = self.peek()
                if arg and re.fullmatch(r"\d+", arg):
                    translation = int(self.take())
                self.index = save
        if depth != 0:
            raise ValueError("unterminated Lua function")
        if translation is not None:
            return {"__translation__": translation}
        # Preserve a deterministic marker without attempting to execute code.
        return {"__function__": "skipped", "token_count": self.index - start}

    def value(self) -> Any:
        token = self.peek()
        if token is None:
            raise ValueError("missing Lua table value")
        if token == "{":
            return self.table()
        if token == "function":
            return self._function_value()
        token = self.take()
        if token.startswith(("'", '"')):
            return ast.literal_eval(token)
        if token in {"true", "false"}:
            return token == "true"
        if token == "nil":
            return None
        if re.fullmatch(r"(?:0[xX][0-9A-Fa-f]+|\d+)", token):
            return int(token, 16) if token.lower().startswith("0x") else int(token, 10)
        if token == "T" and self.peek() == "(":
            self.take()
            argument = self.take()
            if self.take() != ")" or not argument.isdecimal():
                raise ValueError("unsupported translation expression")
            return {"__translation__": int(argument)}
        return token

    def table(self) -> Any:
        if self.take() != "{":
            raise ValueError("expected Lua table")
        fields: dict[Any, Any] = {}
        array: list[Any] = []
        while True:
            token = self.peek()
            if token is None:
                raise ValueError("unterminated Lua table")
            if token == "}":
                self.take()
                break
            if token in {",", ";"}:
                self.take()
                continue
            if token == "[":
                key = self._read_key()
                fields[key] = self.value()
            elif re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", token) and self.peek(1) == "=":
                key = self.take()
                self.take()
                fields[key] = self.value()
            else:
                array.append(self.value())
        if fields:
            fields.update({index: value for index, value in enumerate(array, 1)})
            return fields
        return array


def parse_lua_table(source: str, table_name: str) -> dict[Any, Any]:
    """Parse a named top-level Lua table and `Table[id] = {...}` entries.

    This is a static data reader, not a Lua interpreter. Unsupported syntax is
    rejected and function bodies are skipped; only `T(integer)` is retained.
    """
    tokens = _tokens(source)
    result: dict[Any, Any] = {}
    found = False
    index = 0
    while index < len(tokens):
        if tokens[index] != table_name:
            index += 1
            continue
        cursor = index + 1
        if cursor >= len(tokens):
            break
        if tokens[cursor] == "=":
            parser = _TableParser(tokens)
            parser.index = cursor + 1
            if parser.peek() == "{":
                value = parser.value()
                if isinstance(value, dict):
                    result.update(value)
                    found = True
                index = parser.index
                continue
            index = cursor + 1
            continue
        if tokens[cursor] == "[" and _is_table_entry_assignment(tokens, cursor):
            parser = _TableParser(tokens)
            parser.index = cursor
            key = parser._read_key()
            value = parser.value()
            result[key] = value
            found = True
            index = parser.index
            continue
        index += 1
    if not found:
        raise ValueError(f"Lua table {table_name} not found")
    return result


def _is_table_entry_assignment(tokens: list[str], bracket_index: int) -> bool:
    """Return whether a bracketed expression is followed by an assignment.

    Table names can also occur in executable code, e.g. `Words[locale .. id]`.
    Such reads are not table declarations and must not be passed to the narrower
    literal-key parser.
    """
    depth = 0
    for index in range(bracket_index, len(tokens)):
        token = tokens[index]
        if token == "[":
            depth += 1
        elif token == "]":
            depth -= 1
            if depth == 0:
                return index + 1 < len(tokens) and tokens[index + 1] == "="
    return False


def load_gacha_tables(lua_dir: str) -> tuple[list[dict[str, Any]], dict[Any, Any]]:
    """Load decoded BaseGacha and BaseGachaBottomUp Lua files from one directory."""
    from pathlib import Path

    root = Path(lua_dir)
    by_lower_name = {path.name.lower(): path for path in root.iterdir() if path.is_file()}
    gacha_path = by_lower_name.get("basegacha.lua")
    bottomup_path = by_lower_name.get("basegachabottomup.lua")
    missing = [
        name for name, path in (
            ("basegacha.lua", gacha_path),
            ("basegachabottomup.lua", bottomup_path),
        ) if path is None
    ]
    if missing:
        raise FileNotFoundError(f"卡池 Lua 文件缺失: {', '.join(missing)}")
    pool_map = parse_lua_table(gacha_path.read_text(encoding="utf-8", errors="replace"), "BaseGacha")
    bottomups = parse_lua_table(
        bottomup_path.read_text(encoding="utf-8", errors="replace"), "BaseGachaBottomUp"
    )
    pools: list[dict[str, Any]] = []
    for key, value in pool_map.items():
        if not isinstance(value, dict):
            continue
        normalized = dict(value)
        normalized.setdefault("id", key)
        for list_field in ("card_ids", "main_card_ids"):
            field_value = normalized.get(list_field)
            if isinstance(field_value, dict):
                numeric_keys = sorted(
                    (item_key for item_key in field_value if isinstance(item_key, int)),
                )
                if numeric_keys == list(range(1, len(field_value) + 1)) and len(numeric_keys) == len(field_value):
                    normalized[list_field] = [field_value[item_key] for item_key in numeric_keys]
        pools.append(normalized)
    pools.sort(key=lambda item: (_int_sort(item.get("sort")), _int_sort(item.get("id"))))
    return pools, bottomups


def _int_sort(value: Any) -> tuple[int, int | str]:
    try:
        return 0, int(value)
    except (TypeError, ValueError):
        return 1, str(value)


def load_character_names(lua_dir: str) -> dict[str, str]:
    """Resolve character IDs through BaseCard.name() and BaseWord_cn text."""
    from pathlib import Path

    root = Path(lua_dir)
    paths = {path.name.lower(): path for path in root.iterdir() if path.is_file()}
    card_path = paths.get("basecard.lua")
    word_path = paths.get("baseword_cn.lua")
    if card_path is None or word_path is None:
        return {}
    cards = parse_lua_table(card_path.read_text(encoding="utf-8", errors="replace"), "BaseCard")
    words = parse_lua_table(word_path.read_text(encoding="utf-8", errors="replace"), "BaseWord_cn")
    resolved: dict[str, str] = {}
    for character_id, record in cards.items():
        if not isinstance(record, dict):
            continue
        reference = record.get("name")
        if isinstance(reference, dict):
            word_id = reference.get("__translation__")
            word = words.get(word_id, words.get(str(word_id)))
            if isinstance(word, dict):
                reference = word.get("name")
            else:
                reference = None
        if isinstance(reference, str) and reference.strip():
            resolved[str(character_id)] = reference.strip()
    return resolved
