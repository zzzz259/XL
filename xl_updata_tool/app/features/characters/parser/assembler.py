# -*- coding: utf-8 -*-
"""角色解析聚合器：装配各职责解析器并保持稳定的加载入口。"""

import os
import re

from app.platform.diagnostics import logger

from .cards import parse_basecard_file
from .progression import (
    get_breakthrough_cost,
    get_normal_skill_upgrade_cost,
    get_passive_skill_upgrade_cost,
    parse_quality_up_file_with_cost,
    parse_skill_level_up_file_with_cost,
)
from .skills import parse_t_function_params, process_t_function_params
from .words import (
    parse_badge_suit_file,
    parse_cv_file,
    parse_item_file,
    parse_level_up_file,
    parse_word_file,
)

CHARACTER_ID_MIN = 80100001
CHARACTER_ID_MAX = 80101999


def _parse_skill_maps(skill_file_path, skill_level_up_file_path, word_dict):
    skill_name_map = {}
    skill_desc_map = {}
    skill_to_upgrade = {}
    if not (os.path.isfile(skill_file_path) and os.path.isfile(skill_level_up_file_path)):
        logger.warning(
            "BaseSkill.lua 或 BaseSkillLevelUp.lua 缺失，技能数据为空 "
            f"（sk={os.path.isfile(skill_file_path)}, slu={os.path.isfile(skill_level_up_file_path)}）"
        )
        return skill_name_map, skill_desc_map, skill_to_upgrade

    with open(skill_file_path, 'r', encoding='utf-8') as handle:
        skill_content = handle.read()
    for match in re.finditer(
        r'\[\s*(\d+)\s*\]\s*=\s*\{[^}]*?name\s*=\s*function\(\)\s*return\s*T\((\d+)\)\s*end',
        skill_content,
    ):
        skill_name_map[int(match.group(1))] = word_dict.get(int(match.group(2)), match.group(1))

    with open(skill_level_up_file_path, 'r', encoding='utf-8') as handle:
        level_content = handle.read()
    level_pattern = re.compile(r'\[\s*(\d+)\s*\]\s*=\s*\{[^}]*?des\s*=\s*function\(\)\s*return\s*T\(')
    for match in level_pattern.finditer(level_content):
        upgrade_id = int(match.group(1))
        start = match.end()
        depth = 1
        pos = start
        while pos < len(level_content) and depth > 0:
            if level_content[pos] == '(':
                depth += 1
            elif level_content[pos] == ')':
                depth -= 1
            pos += 1
        if depth != 0:
            continue
        params = process_t_function_params(parse_t_function_params(level_content[start:pos - 1].strip()), word_dict)
        if not params:
            continue
        try:
            main_text = word_dict.get(int(params[0]), f"未知({params[0]})")
            for param in params[1:]:
                if "%s" in main_text:
                    main_text = main_text.replace("%s", str(param), 1)
                elif "%d" in main_text:
                    main_text = main_text.replace("%d", str(param), 1)
            main_text = re.sub(r'\[color=#[0-9a-fA-F]+\]|\[/color\]', '', main_text)
            skill_desc_map[upgrade_id] = main_text.replace("%%", "%").replace("\\n", "\n")
        except (ValueError, IndexError):
            logger.debug(f"技能描述解析失败: upgrade_id={upgrade_id}")
    for upgrade_id in sorted(skill_desc_map):
        skill_to_upgrade.setdefault(upgrade_id // 1000, upgrade_id)
    return skill_name_map, skill_desc_map, skill_to_upgrade


def load_character_data(lua_dir, progress_callback=None):
    """从解密后的 Lua 文件加载角色数据，保持既有返回值和进度回调语义。"""
    def report(progress, message):
        if progress_callback:
            progress_callback(progress, message)

    if not os.path.isdir(lua_dir):
        logger.warning(f"Lua 目录不存在: {lua_dir}")
        return [], {}, {}
    word_path = os.path.join(lua_dir, "BaseWord_cn.lua")
    if not os.path.isfile(word_path):
        logger.warning("BaseWord_cn.lua 不存在, 角色数据无法加载")
        return [], {}, {}

    report(10, "正在解析 BaseWord_cn.lua...")
    word_dict = parse_word_file(word_path)
    logger.info(f"BaseWord 文本映射提取: {len(word_dict)} 条")
    report(20, "正在解析 BaseCvNameCn.lua...")
    cv_path = os.path.join(lua_dir, "BaseCvNameCn.lua")
    cv_dict = parse_cv_file(cv_path) if os.path.isfile(cv_path) else {}
    logger.info(f"BaseCvNameCn 解析完成: {len(cv_dict)} 条")
    report(30, "正在解析 BaseCardLevelUp.lua...")
    level_path = os.path.join(lua_dir, "BaseCardLevelUp.lua")
    level_dict = parse_level_up_file(level_path) if os.path.isfile(level_path) else {}
    logger.info(f"BaseCardLevelUp 解析完成: {len(level_dict)} 条")
    report(40, "正在解析 BaseCardQualityUp.lua...")
    quality_path = os.path.join(lua_dir, "BaseCardQualityUp.lua")
    quality_dict = parse_quality_up_file_with_cost(quality_path) if os.path.isfile(quality_path) else {}
    logger.info(f"BaseCardQualityUp 解析完成: {len(quality_dict)} 条")

    report(50, "正在解析 BaseSkill + BaseSkillLevelUp...")
    skill_path = os.path.join(lua_dir, "BaseSkill.lua")
    skill_level_path = os.path.join(lua_dir, "BaseSkillLevelUp.lua")
    skill_name_map, skill_desc_map, _ = _parse_skill_maps(skill_path, skill_level_path, word_dict)
    logger.info(f"BaseSkill 解析: {len(skill_name_map)} 个技能名称, {len(skill_desc_map)} 条描述")

    report(60, "正在解析 BaseBadgeSuitGroup.lua...")
    badge_path = os.path.join(lua_dir, "BaseBadgeSuitGroup.lua")
    badge_dict = parse_badge_suit_file(badge_path, word_dict) if os.path.isfile(badge_path) else {}
    logger.info(f"BaseBadgeSuitGroup 解析完成: {len(badge_dict)} 套")
    report(70, "正在解析 BaseItem.lua...")
    item_path = os.path.join(lua_dir, "BaseItem.lua")
    item_dict = parse_item_file(item_path, word_dict) if os.path.isfile(item_path) else {}
    logger.info(f"BaseItem 解析完成: {len(item_dict)} 个物品")
    report(80, "正在解析技能升级消耗...")
    skill_cost_dict = parse_skill_level_up_file_with_cost(skill_level_path) if os.path.isfile(skill_level_path) else {}
    logger.info(f"BaseSkillLevelUp 消耗解析完成: {len(skill_cost_dict)} 条")

    report(90, "正在解析 BaseCard.lua...")
    card_path = os.path.join(lua_dir, "BaseCard.lua")
    if not os.path.isfile(card_path):
        logger.warning("BaseCard.lua 不存在")
        return [], {}, word_dict
    characters_full = parse_basecard_file(
        card_path, word_dict, cv_dict, level_dict, quality_dict,
        skill_path, skill_level_path, badge_dict,
    )
    for char_id, char_data in characters_full.items():
        char_data['breakthrough_costs'] = get_breakthrough_cost(char_id, quality_dict, item_dict)
        normal_skill_id = char_data.get('normal_skill_id', 0)
        char_data['normal_skill_upgrade_costs'] = (
            get_normal_skill_upgrade_cost(normal_skill_id, skill_cost_dict, item_dict)
            if normal_skill_id else ["", "", ""]
        )
        passive_skill_id = char_data.get('first_passive_skill_id', 0)
        char_data['passive_skill_upgrade_costs'] = (
            get_passive_skill_upgrade_cost(passive_skill_id, skill_cost_dict, item_dict)
            if passive_skill_id else ["", "", ""]
        )

    filtered = {
        char_id: data for char_id, data in characters_full.items()
        if CHARACTER_ID_MIN <= data.get("raw_id", 0) <= CHARACTER_ID_MAX
    }
    characters = [
        {
            "name": data.get("name", "未知").split('/')[0],
            "char_id": char_id,
            "raw_id": data.get("raw_id", 0),
            "display_index": data.get("raw_id", 0),
        }
        for char_id, data in sorted(filtered.items(), key=lambda item: item[1].get("raw_id", 0))
    ]
    return characters, characters_full, word_dict
