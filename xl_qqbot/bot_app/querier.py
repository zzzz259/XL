"""@机器人+角色名 的图鉴查询：匹配角色并定位已渲染长图。"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from pathlib import Path

_logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class QueryResult:
    status: str  # "found" | "not_found" | "no_image"
    character_id: str = ""
    name: str = ""
    image_path: Path | None = None


class CharacterQuerier:
    def __init__(self, character_data: str | Path, versions_dir: str | Path):
        self._character_data = Path(character_data)
        self._versions_dir = Path(versions_dir)
        self._cache_mtime: float | None = None
        self._characters: dict = {}

    def _load(self) -> dict:
        try:
            mtime = self._character_data.stat().st_mtime
        except OSError:
            return {}
        if mtime != self._cache_mtime:
            try:
                payload = json.loads(self._character_data.read_text(encoding="utf-8"))
            except (OSError, ValueError):
                return {}
            self._characters = payload.get("characters", {})
            self._cache_mtime = mtime
        return self._characters

    def _match(self, query: str) -> tuple[str, dict] | None:
        characters = self._load()
        lowered = query.lower()
        for character_id, data in characters.items():
            name = str(data.get("name", ""))
            cn_name, _, en_name = name.partition("/")
            if query == cn_name or query == str(character_id):
                return str(character_id), data
            if en_name and lowered == en_name.lower():
                return str(character_id), data
        return None

    def _image_for(self, character_id: str) -> Path | None:
        pointer = self._character_data.parent.parent / "current_version.json"
        try:
            version = json.loads(pointer.read_text(encoding="utf-8"))["version"]
        except (OSError, ValueError, KeyError):
            return None
        cards_dir = self._versions_dir / str(version) / "character_cards"
        if not cards_dir.is_dir():
            return None
        matches = sorted(cards_dir.glob(f"{character_id}_*"))
        return matches[0] if matches else None

    def find(self, query: str) -> QueryResult:
        query = query.strip().strip("\"'“”‘’").strip()
        if not query:
            return QueryResult(status="not_found")
        matched = self._match(query)
        if matched is None:
            return QueryResult(status="not_found")
        character_id, data = matched
        cn_name = str(data.get("name", "")).split("/", 1)[0]
        image = self._image_for(character_id)
        if image is None:
            return QueryResult(status="no_image", character_id=character_id, name=cn_name)
        return QueryResult(status="found", character_id=character_id, name=cn_name, image_path=image)
