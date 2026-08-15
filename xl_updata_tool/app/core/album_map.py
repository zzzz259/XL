# -*- coding: utf-8 -*-
"""解析音乐专辑映射：bgm 文件名 -> 专辑名

链路（三张反编译后的 lua 表）：
  BaseSound.lua         song_id -> bank 路径（含 bgm 文件名）
  BaseSoundChapter.lua  album_id -> name(T 文本) + child_ids(song_id 列表)
  BaseWord_cn.lua       text_id -> 中文名（如 80880001 -> 星落）

输入：反编译后的 lua 目录（data/material/assets/lua/）
输出：{bgm文件名: 专辑名}
"""

import os
import re

from app.core.character_loader import parse_word_file
from app.core.logger import logger


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


def build_album_map(lua_dir):
    """从反编译的 lua 文件解析专辑映射，返回 {bgm文件名: 专辑名}。

    任一文件缺失时返回空 dict（调用方回退到按子目录分）。
    """
    word_path = os.path.join(lua_dir, "BaseWord_cn.lua")
    sound_path = os.path.join(lua_dir, "BaseSound.lua")
    chapter_path = os.path.join(lua_dir, "BaseSoundChapter.lua")
    for p in (word_path, sound_path, chapter_path):
        if not os.path.exists(p):
            logger.info(f"[专辑映射] 缺少 {os.path.basename(p)}，跳过专辑名解析")
            return {}

    logger.debug(f"[专辑映射] 开始解析: {lua_dir}")
    try:
        # 1. 文本映射 text_id -> 中文名
        word_map = parse_word_file(word_path)
        logger.debug(f"[专辑映射] 文本映射 {len(word_map)} 条")

        # 2. BaseSound: song_id -> bank 文件名
        with open(sound_path, encoding="utf-8") as f:
            sound_text = f.read()
        song_banks = {}
        for song_id, block in _split_lua_blocks(sound_text):
            bank = re.search(r'bank\s*=\s*"bank:/([^"]+)"', block)
            if bank:
                song_banks[song_id] = bank.group(1).split("/")[-1]
        logger.debug(f"[专辑映射] song->bank {len(song_banks)} 条")

        # 3. BaseSoundChapter: album_id -> name(T) + child_ids
        with open(chapter_path, encoding="utf-8") as f:
            chapter_text = f.read()
        album_map = {}
        for _album_id, block in _split_lua_blocks(chapter_text):
            name_t = re.search(r'T\((\d+)\)', block)
            child_ids = re.search(r'child_ids\s*=\s*\{([^}]*)\}', block)
            if not (name_t and child_ids):
                continue
            album_name = word_map.get(int(name_t.group(1)))
            if not album_name:
                continue
            for cid in re.findall(r'\d+', child_ids.group(1)):
                bank_name = song_banks.get(cid)
                if bank_name:
                    album_map[bank_name] = album_name

        logger.info(f"[专辑映射] 解析到 {len(album_map)} 个 bgm -> 专辑 映射")
        return album_map
    except Exception as e:
        logger.error(f"[专辑映射] 解析失败: {e}", exc_info=True)
        return {}
