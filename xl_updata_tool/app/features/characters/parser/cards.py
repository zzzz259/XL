# -*- coding: utf-8 -*-
"""BaseCard 角色实体聚合解析。"""

import re

from app.platform.diagnostics import logger

from .skills import (
    extract_awakening_skill_info,
    extract_multi_level_skill_info_new,
    extract_skill_info,
)

ELEMENT_MAP = {1: "水属性", 2: "火属性", 3: "木属性", 4: "暗属性", 5: "光属性"}


def _read(file_path):
    with open(file_path, 'r', encoding='utf-8') as handle:
        return handle.read()


def _story_text(text):
    text = re.sub(r'<[^>]+>', '', text)
    text = re.sub(r'&[a-z]+;', '', text)
    text = re.sub(r'<style[^>]*>.*?</style>', '', text, flags=re.DOTALL)
    text = re.sub(r'<script[^>]*>.*?</script>', '', text, flags=re.DOTALL)
    text = re.sub(r'<!--.*?-->', '', text, flags=re.DOTALL)
    text = re.sub(r'<p style=\'text-align: right;\'>.*?</p>', '', text)
    text = re.sub(r'<span.*?>.*?</span>', '', text)
    text = re.sub(r'<.*?>', '', text)
    return text.replace("\\n", "\n").strip()


def _value(data, pattern, default="未知"):
    match = re.search(pattern, data)
    return match.group(1) if match else default


def parse_basecard_file(file_path, word_dict, cv_dict, level_up_dict,
                        quality_up_dict, skill_file_path, skill_level_up_file_path,
                        badge_suit_dict):
    """完整解析 BaseCard.lua，返回角色数据字典。"""
    type_mapping = {1: "坚甲", 2: "异刃", 4: "言灵", 5: "猎影"}
    content = _read(file_path)
    characters = {}
    pattern = r'\[(\d+)\] = (\{(?:[^{}]|(?:\{(?:[^{}]|(?:\{[^{}]*\}))*\}))*\})'

    for char_id_text, char_data in re.findall(pattern, content, re.DOTALL):
        char_id = int(char_id_text)
        normal_skill_match = re.search(r'normal_skill\s*=\s*(\d+)', char_data)
        normal_skill_id = int(normal_skill_match.group(1)) if normal_skill_match else 0
        grow_skills_match = re.search(r'grow_skill_ids\s*=\s*\{([^}]*)\}', char_data)
        grow_skill_ids = re.findall(r'(\d+)', grow_skills_match.group(1)) if grow_skills_match else []
        first_passive_skill_id = int(grow_skill_ids[0]) if grow_skill_ids else 0

        name_match = re.search(r'name = function\(\)\s*return T\((\d+)\)\s*end', char_data)
        english_match = re.search(r'name_english = function\(\)\s*return T\((\d+)\)\s*end', char_data)
        if not (name_match and english_match):
            continue
        name_key = int(name_match.group(1))
        english_key = int(english_match.group(1))
        chinese_name = word_dict.get(name_key, f"未知({name_key})")
        english_name = word_dict.get(english_key, f"Unknown({english_key})")

        star = _value(char_data, r'star\s*=\s*(\d+)')
        init_hp = int(_value(char_data, r'max_hp\s*=\s*(\d+)', 0))
        init_atk = int(_value(char_data, r'atk\s*=\s*(\d+)', 0))
        init_def = int(_value(char_data, r'def\s*=\s*(\d+)', 0))
        grow_model_id = int(_value(char_data, r'grow_model_id\s*=\s*(\d+)', 0))
        quality_max = int(_value(char_data, r'quality_max\s*=\s*(\d+)', 0))
        max_hp, max_atk, max_def = init_hp, init_atk, init_def
        if grow_model_id:
            level_attr = level_up_dict.get(grow_model_id * 1000 + 340, {})
            quality_attr = quality_up_dict.get(char_id * 1000 + quality_max, {})
            quality_attr = quality_attr.get('add_attr', {}) if isinstance(quality_attr, dict) else {}
            max_hp += level_attr.get("生命", 0) + quality_attr.get("生命", 0)
            max_atk += level_attr.get("攻击", 0) + quality_attr.get("攻击", 0)
            max_def += level_attr.get("防御", 0) + quality_attr.get("防御", 0)

        type_match = re.search(r'type\s*=\s*(\d+)', char_data)
        profession = type_mapping.get(int(type_match.group(1)), f"未知({type_match.group(1)})") if type_match else "未知"
        element_match = re.search(r'element_type\s*=\s*\{(\d+)\}', char_data)
        element = ELEMENT_MAP.get(int(element_match.group(1)), f"未知({element_match.group(1)})") if element_match else "未知"

        birthday_match = re.search(r'information1\s*=\s*function\(\)\s*return\s*T\(\d+,\s*(\d+),\s*(\d+)\)\s*end', char_data)
        birthday = f"{birthday_match.group(1)}/{birthday_match.group(2)}" if birthday_match else "未知"
        height_match = re.search(r'information2\s*=\s*function\(\)\s*return\s*T\(\d+,\s*(\d+)\)\s*end', char_data)
        height = f"{height_match.group(1)}cm" if height_match else "未知"
        faction_match = re.search(r'information3\s*=\s*function\(\)\s*return\s*T\((\d+)\)\s*end', char_data)
        faction = word_dict.get(int(faction_match.group(1)), f"未知({faction_match.group(1)})") if faction_match else "未知"
        cv_match = re.search(r'cv_name\s*=\s*(\d+)', char_data)
        cv_info = cv_dict.get(int(cv_match.group(1)), f"未知({cv_match.group(1)})") if cv_match else "未知"

        des_match = re.search(r'des\s*=\s*function\(\)\s*return\s*T\((\d+)\)\s*end', char_data)
        des1_match = re.search(r'des1\s*=\s*function\(\)\s*return\s*T\((\d+)\)\s*end', char_data)
        description = "未知"
        if des_match and des1_match:
            description = f"{word_dict.get(int(des_match.group(1)), f'未知({des_match.group(1)})')}\n——{word_dict.get(int(des1_match.group(1)), f'未知({des1_match.group(1)})')}"

        leader_match = re.search(r'leader_skill\s*=\s*(\d+)', char_data)
        leader_skill = extract_skill_info(int(leader_match.group(1)), skill_file_path, skill_level_up_file_path, word_dict) if leader_match else "未知"
        normal_skill = extract_multi_level_skill_info_new(normal_skill_id, skill_file_path, skill_level_up_file_path, word_dict) if normal_skill_match else "未知"
        special_match = re.search(r'special_skill\s*=\s*(\d+)', char_data)
        special_skill = extract_multi_level_skill_info_new(int(special_match.group(1)), skill_file_path, skill_level_up_file_path, word_dict) if special_match else "未知"
        burst_match = re.search(r'burst_skill\s*=\s*(\d+)', char_data)
        burst_skill = extract_multi_level_skill_info_new(int(burst_match.group(1)), skill_file_path, skill_level_up_file_path, word_dict, is_burst_skill=True) if burst_match else "未知"

        passive_skills = ["未知", "未知", "未知"]
        for index, skill_id in enumerate(grow_skill_ids[:3]):
            passive_skills[index] = extract_multi_level_skill_info_new(int(skill_id), skill_file_path, skill_level_up_file_path, word_dict, 3)

        awakening_skills = ["未知"] * 5
        unlock_match = re.search(r'unlock_skill_ids\s*=\s*\{([^}]*)\}', char_data, re.DOTALL)
        if unlock_match:
            for index, skill_id in enumerate(re.findall(r'(\d+)', unlock_match.group(1))[:5]):
                awakening_skills[index] = extract_awakening_skill_info(int(skill_id), skill_file_path, skill_level_up_file_path, word_dict)

        voice_lines = [''] * 34
        sound_match = re.search(r'sound_ids\s*=\s*\{([^}]*)\}', char_data, re.DOTALL)
        if sound_match:
            sound_ids = re.findall(r'(\d+)', sound_match.group(1))
            if len(sound_ids) == 32:
                positions = [
                    index if index < 4 else index + 1 if index < 20 else index + 2
                    for index in range(32)
                ]
            else:
                positions = list(range(len(sound_ids)))
            for index, sound_id in enumerate(sound_ids):
                if index < len(positions) and positions[index] < 34:
                    voice_lines[positions[index]] = f'"{word_dict.get(int(sound_id), "未知")}"'

        stories = ["未知"] * 4
        story_match = re.search(r'story_ids\s*=\s*\{([^}]*)\}', char_data, re.DOTALL)
        if story_match:
            for index, item in enumerate(re.findall(r'"([^"]+)"', story_match.group(1))[:4]):
                if ':' in item:
                    stories[index] = _story_text(word_dict.get(int(item.split(':')[0]), f"未知({item.split(':')[0]})"))

        badge_info = ""
        badge_suit_match = re.search(r'badge_suit_ids\s*=\s*\{([^}]*)\}', char_data, re.DOTALL)
        badge_main_match = re.search(r'badge_main_attribute\s*=\s*\{([^}]*)\}', char_data, re.DOTALL)
        badge_vice_match = re.search(r'badge_vice_attribute\s*=\s*\{([^}]*)\}', char_data, re.DOTALL)
        if badge_suit_match or badge_main_match or badge_vice_match:
            parts = []
            if badge_suit_match:
                suits = [badge_suit_dict.get(int(value), f"未知({value})") for value in re.findall(r'(\d+)', badge_suit_match.group(1))]
                if suits:
                    parts.extend(["推荐徽章", "/".join(suits), ""])
            if badge_main_match:
                attrs = []
                for item in re.findall(r'"([^"]+)"', badge_main_match.group(1)):
                    ids = item.split(':')
                    attrs.append(' '.join(word_dict.get(int('8' + value[1:]), f"未知({int('8' + value[1:])})") for value in ids))
                if attrs:
                    parts.extend(["推荐主属性", "//".join(attrs), ""])
            if badge_vice_match:
                attrs = [word_dict.get(int('8' + value[1:]), f"未知({int('8' + value[1:])})") for value in re.findall(r'(\d+)', badge_vice_match.group(1))]
                if attrs:
                    parts.extend(["推荐副属性", " ".join(attrs)])
            badge_info = "\n".join(parts)

        extras = {}
        for key in ("crt", "blk", "crt_int", "blk_int", "spd_move", "spd_atk", "range_atk", "weight"):
            extras[key] = _value(char_data, rf'{key}\s*=\s*(\d+)')

        characters[char_id] = {
            "raw_id": name_key,
            "name": f"{chinese_name}/{english_name}",
            "star": star, "profession": profession, "element": element,
            "birthday": birthday, "height": height, "faction": faction, "cv": cv_info,
            "description": description,
            "init_atk": init_atk, "init_def": init_def, "init_hp": init_hp,
            "max_atk": max_atk, "max_def": max_def, "max_hp": max_hp,
            **extras,
            "leader_skill": leader_skill, "normal_skill": normal_skill,
            "special_skill": special_skill, "burst_skill": burst_skill,
            "passive_skill_1": passive_skills[0], "passive_skill_2": passive_skills[1], "passive_skill_3": passive_skills[2],
            "awakening_skill_1": awakening_skills[0], "awakening_skill_2": awakening_skills[1], "awakening_skill_3": awakening_skills[2],
            "awakening_skill_4": awakening_skills[3], "awakening_skill_5": awakening_skills[4],
            **{f"voice_{index + 1}": value for index, value in enumerate(voice_lines)},
            "personal_info": stories[0], "anecdote": stories[1], "record": stories[2], "anecdote2": stories[3],
            "badge_info": badge_info,
            "normal_skill_id": normal_skill_id,
            "first_passive_skill_id": first_passive_skill_id,
        }

    logger.debug(f"BaseCard.lua 解析完成: {len(characters)} 个角色")
    return characters
