# -*- coding: utf-8 -*-
"""解析音乐专辑映射：bank 路径/文件名 -> 专辑名

链路（三张反编译后的 lua 表）：
  BaseSound.lua         song_id -> bank 路径（含 bgm 文件名）
  BaseSoundChapter.lua  album_id -> name(T 文本) + child_ids(song_id 列表)
  BaseWord_cn.lua       text_id -> 中文名（如 80880001 -> 星落）

输入：反编译后的 lua 目录（data/material/assets/lua/）
输出同时包含规范化 bank 路径和文件名别名，兼容不同导出目录。
"""

import json
import os
import re

from app.core.character_loader import parse_word_file
from app.core.logger import logger


def _normalise_bank_key(value):
    """统一 bank 路径，兼容 ``bank:/``、反斜杠和扩展名。"""
    value = str(value or "").strip().replace("\\", "/")
    value = re.sub(r"^bank:/+", "", value, flags=re.IGNORECASE)
    value = value.strip("/").lower()
    if value.endswith(".bank"):
        value = value[:-5]
    return value


def _bank_aliases(value):
    key = _normalise_bank_key(value)
    if not key:
        return set()
    aliases = {key, os.path.basename(key)}
    if "/" in key:
        aliases.add(key.rsplit("/", 1)[-1])
    return aliases


def _split_lua_blocks(text):
    """按顶层 [id] = { ... } 分割（处理嵌套花括号），返回 [(id, block_content), ...]"""
    blocks = []
    for m in re.finditer(r'\[(\d+)\]\s*=\s*\{', text):
        start = m.end()
        depth = 1
        i = start
        while i < len(text) and depth > 0:
            if text[i] == '{':
                depth += 1
            elif text[i] == '}':
                depth -= 1
            i += 1
        blocks.append((m.group(1), text[start:i - 1]))
    return blocks


def _parse_album_bank_entries(lua_dir):
    """解析 ``[(album_name, bank_path), ...]``，供分类和审计共用。"""
    word_path = os.path.join(lua_dir, "BaseWord_cn.lua")
    sound_path = os.path.join(lua_dir, "BaseSound.lua")
    chapter_path = os.path.join(lua_dir, "BaseSoundChapter.lua")
    for p in (word_path, sound_path, chapter_path):
        if not os.path.exists(p):
            logger.info(f"[专辑映射] 缺少 {os.path.basename(p)}，跳过专辑名解析")
            return []

    logger.debug(f"[专辑映射] 开始解析: {lua_dir}")
    try:
        # 1. 文本映射 text_id -> 中文名
        word_map = parse_word_file(word_path)
        logger.debug(f"[专辑映射] 文本映射 {len(word_map)} 条")

        # 2. BaseSound: song_id -> bank 完整路径
        with open(sound_path, encoding="utf-8") as f:
            sound_text = f.read()
        song_banks = {}
        for song_id, block in _split_lua_blocks(sound_text):
            bank = re.search(r'bank\s*=\s*"(bank:/[^"]+)"', block)
            if bank:
                song_banks[song_id] = bank.group(1)
        logger.debug(f"[专辑映射] song->bank {len(song_banks)} 条")

        # 3. BaseSoundChapter: album_id -> name(T) + child_ids
        with open(chapter_path, encoding="utf-8") as f:
            chapter_text = f.read()
        entries = []
        for _album_id, block in _split_lua_blocks(chapter_text):
            name_t = re.search(r'T\((\d+)\)', block)
            child_ids = re.search(r'child_ids\s*=\s*\{([^}]*)\}', block)
            if not (name_t and child_ids):
                continue
            album_name = word_map.get(int(name_t.group(1)))
            if not album_name:
                continue
            for cid in re.findall(r'\d+', child_ids.group(1)):
                bank_path = song_banks.get(cid)
                if bank_path:
                    entries.append((album_name, bank_path))

        logger.info(f"[专辑映射] 解析到 {len(entries)} 个 BGM 配置项")
        return entries
    except Exception as e:
        logger.error(f"[专辑映射] 解析失败: {e}", exc_info=True)
        return []


def build_album_bank_map(lua_dir):
    """返回 ``{专辑名: {规范化 bank 路径}}``，用于完整性审计。"""
    result = {}
    for album_name, bank_path in _parse_album_bank_entries(lua_dir):
        result.setdefault(album_name, set()).add(_normalise_bank_key(bank_path))
    return result


def build_album_map(lua_dir):
    """从反编译的 lua 文件解析专辑映射，返回 ``{bank别名: 专辑名}``。"""
    album_map = {}
    for album_name, bank_path in _parse_album_bank_entries(lua_dir):
        for alias in _bank_aliases(bank_path):
            album_map[alias] = album_name
    logger.info(f"[专辑映射] 解析到 {len(album_map)} 个 bgm -> 专辑 映射")
    return album_map


def _state_bank_key(value):
    """将 bank 状态中的 material 相对路径裁剪为 ``bgm/...``。"""
    key = _normalise_bank_key(value)
    marker = "/bgm/"
    if marker in key:
        return key[key.index("bgm/"):]
    return key


def audit_bgm_exports(lua_dir, audio_output_dir, state_path=None):
    """对照 Lua 专辑配置和 bank 增量状态，返回 BGM 导出缺口报告。

    bank 状态记录比直接按 WAV 文件名猜测来源可靠：一个 bank 可能包含多个
    subsong，且提取器输出名通常是事件名而不是 bank 文件名。
    """
    expected_by_album = build_album_bank_map(lua_dir)
    report = {
        "available": bool(expected_by_album),
        "expected_by_album": expected_by_album,
        "missing_by_album": {},
        "untracked": [],
        "misclassified": [],
        "state_available": False,
    }
    if not expected_by_album:
        return report

    state_file = state_path or os.path.join(audio_output_dir, ".bank_state.json")
    try:
        with open(state_file, encoding="utf-8") as handle:
            state = json.load(handle)
    except (OSError, ValueError, TypeError):
        return report

    banks = state.get("banks", {}) if isinstance(state, dict) else {}
    if not isinstance(banks, dict):
        return report
    report["state_available"] = True

    present = {album: set() for album in expected_by_album}
    for source_path, record in banks.items():
        if not isinstance(record, dict):
            continue
        out_rel = str(record.get("out_rel", "")).replace("\\", "/")
        files = record.get("files", [])
        if not out_rel.startswith("album/") or not files:
            continue
        files_are_valid = all(
            isinstance(item, dict)
            and os.path.isfile(os.path.join(audio_output_dir, str(item.get("path", ""))))
            and os.path.getsize(os.path.join(audio_output_dir, str(item.get("path", "")))) > 0
            for item in files
        )
        if not files_are_valid:
            continue
        key = _state_bank_key(source_path)
        matched_album = None
        for album_name, expected in expected_by_album.items():
            if key in expected:
                matched_album = album_name
                actual_album = out_rel.split("/", 2)[1] if out_rel.count("/") >= 1 else ""
                if actual_album == album_name:
                    present[album_name].add(key)
                else:
                    report["misclassified"].append({
                        "bank": key,
                        "expected": album_name,
                        "actual": actual_album,
                    })
                break
        if matched_album is None:
            report["untracked"].append({"bank": key, "output": out_rel})

    for album_name, expected in expected_by_album.items():
        missing = sorted(expected - present[album_name])
        if missing:
            report["missing_by_album"][album_name] = missing
    return report
