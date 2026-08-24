"""角色功能域数据服务。

该服务集中封装角色 Lua 解析、版本仓库、旧缓存兼容和 CSV 导出；不依赖 Qt，
控制器只负责把结果投影到 CharacterPage。
"""

from __future__ import annotations

import os
from pathlib import Path

from app.core.character_cache import (
    derive_character_index,
    load_cache,
    save_cache,
    source_mtime,
)
from app.core.character_loader import load_character_data
from app.core.character_presenter import export_characters_csv
from app.core.character_repository import (
    clear_all_unread,
    clear_unread,
    current_characters,
    load_repository,
    merge_snapshot,
    repository_path,
    unread_status,
)
from app.core.lua_repository import (
    has_character_sources,
    latest_lua_version,
    should_auto_parse,
    version_directory,
)


CHARACTER_SOURCE_FILES = [
    "BaseWord_cn.lua",
    "BaseCvNameCn.lua",
    "BaseCardLevelUp.lua",
    "BaseCardQualityUp.lua",
    "BaseSkill.lua",
    "BaseSkillLevelUp.lua",
    "BaseBadgeSuitGroup.lua",
    "BaseItem.lua",
    "BaseCard.lua",
]


class CharacterService:
    """角色数据仓库和 Lua 解析的 Qt-free 门面。"""

    def __init__(self, data_dir: str | os.PathLike[str], lua_output_dir: str | os.PathLike[str]):
        self.data_dir = Path(data_dir)
        self.lua_output_dir = Path(lua_output_dir)
        self.repository_file = Path(repository_path(self.data_dir))
        self.cache_file = self.data_dir / "characters_full.json"

    def load_repository(self) -> dict:
        return load_repository(self.repository_file)

    def load_local(self) -> dict | None:
        """只读取仓库/旧缓存，不触发 Lua 解析。"""
        repository = self.load_repository()
        characters = current_characters(repository)
        if characters:
            return {
                "characters_full": characters,
                "unread": unread_status(repository),
                "version": repository.get("current_version"),
                "source": "repository",
            }

        cached = self.load_cache(validate_source=False)
        if not cached:
            return None
        return {
            "characters_full": cached,
            "unread": unread_status(repository),
            "version": None,
            "source": "cache",
        }

    def latest_source(self) -> tuple[int | None, str | None]:
        version = latest_lua_version(str(self.lua_output_dir))
        if version is not None:
            return version, version_directory(str(self.lua_output_dir), version)
        if has_character_sources(str(self.lua_output_dir)):
            return None, str(self.lua_output_dir)
        return None, None

    @staticmethod
    def has_source(lua_dir: str | os.PathLike[str] | None) -> bool:
        return bool(lua_dir and has_character_sources(str(lua_dir)))

    def parse(self, lua_dir: str, on_progress=None):
        return load_character_data(lua_dir, on_progress)

    def merge_version(
        self,
        version_timestamp: int | str,
        characters_full: dict,
        source_dir: str,
        baseline_characters: dict | None = None,
    ) -> dict:
        return merge_snapshot(
            self.data_dir,
            version_timestamp,
            characters_full,
            source_dir=source_dir,
            baseline_characters=baseline_characters,
        )

    def derive_index(self, characters_full: dict, unread: dict[str, str]) -> list[dict]:
        characters = derive_character_index(characters_full)
        for item in characters:
            item["change_status"] = unread.get(str(item.get("char_id")))
        return characters

    def source_mtime(self, lua_dir: str | os.PathLike[str]) -> float:
        return source_mtime(str(lua_dir), CHARACTER_SOURCE_FILES)

    def save_legacy_cache(self, characters_full: dict, lua_dir: str | os.PathLike[str]) -> None:
        self.cache_file.parent.mkdir(parents=True, exist_ok=True)
        save_cache(str(self.cache_file), characters_full, self.source_mtime(lua_dir))

    def load_cache(self, lua_dir: str | os.PathLike[str] | None = None, validate_source: bool = True):
        source_dir = str(lua_dir or self.lua_output_dir)
        return load_cache(
            str(self.cache_file),
            self.source_mtime(source_dir),
            validate_source=validate_source,
        )

    def clear_unread(self, char_id) -> bool:
        return clear_unread(self.data_dir, char_id)

    def clear_all_unread(self) -> int:
        return clear_all_unread(self.data_dir)

    def export_csv(self, file_path: str | os.PathLike[str], characters_full: dict) -> int:
        return export_characters_csv(str(file_path), characters_full)

    def should_auto_parse(self, version, latest_version, lua_dir) -> bool:
        return should_auto_parse(version, latest_version, lua_dir)
