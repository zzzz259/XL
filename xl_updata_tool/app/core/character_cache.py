"""角色数据缓存与索引派生，不依赖 Qt。"""

import json
import os
from collections.abc import Mapping

from .logger import logger


def source_mtime(lua_dir: str, source_files: list[str]) -> float:
    """返回角色源 Lua 文件最大修改时间，缺失或读取失败返回 0。"""
    latest = 0.0
    for filename in source_files:
        path = os.path.join(lua_dir, filename)
        if os.path.isfile(path):
            try:
                latest = max(latest, os.path.getmtime(path))
            except OSError:
                pass
    return latest


def derive_character_index(characters_full: Mapping) -> list[dict]:
    """从完整角色数据派生 UI 表格所需的轻量索引。"""
    characters = []
    for char_id, info in sorted(characters_full.items(), key=lambda item: item[1].get("raw_id", 0)):
        raw_id = info.get("raw_id", 0)
        if 80100001 <= raw_id <= 80101999:
            characters.append({
                "name": str(info.get("name", "未知")).split("/")[0],
                "char_id": char_id,
                "raw_id": raw_id,
                "display_index": raw_id,
            })
    return characters


def save_cache(cache_path: str, characters_full: Mapping, source_timestamp: float) -> None:
    """保存完整角色数据和源文件版本时间。"""
    os.makedirs(os.path.dirname(cache_path), exist_ok=True)
    payload = {"source_mtime": source_timestamp, "characters_full": characters_full}
    with open(cache_path, "w", encoding="utf-8") as handle:
        json.dump(payload, handle, ensure_ascii=False, indent=2, default=str)


def load_cache(cache_path: str, source_timestamp: float, validate_source: bool = True):
    """读取角色缓存；迁移旧缓存时可关闭源文件时间校验。"""
    if not os.path.isfile(cache_path):
        return None
    try:
        with open(cache_path, encoding="utf-8") as handle:
            payload = json.load(handle)
        if not isinstance(payload, dict):
            return None
        if validate_source and payload.get("source_mtime", 0) != source_timestamp:
            logger.info("角色源 lua 有更新，缓存失效，重新解析")
            return None
        characters_full = payload.get("characters_full")
        return characters_full if isinstance(characters_full, dict) and characters_full else None
    except (OSError, ValueError, TypeError) as exc:
        logger.warning("读取角色缓存失败: %s", exc)
        return None
