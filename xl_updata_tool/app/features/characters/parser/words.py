# -*- coding: utf-8 -*-
"""角色解析所需的文本、声优、养成和物品表解析。"""

import re

from app.shared.lua import parse_word_file as parse_word_file


def parse_cv_file(file_path):
    """解析 BaseCvNameCn.lua，返回 cv_dict (id -> 中文名/日文名)。"""
    result = {}
    content = file_path.read_text(encoding="utf-8") if hasattr(file_path, "read_text") else open(file_path, encoding="utf-8").read()
    pattern = r'\[(\d+)\] = \{(.*?)\}(?=,|\n\s*\]|\n\s*\})'
    for cv_id, cv_data in re.findall(pattern, content, re.DOTALL):
        cn_match = re.search(r'name_cn = "([^"]+)"', cv_data)
        jp_match = re.search(r'name_jp = "([^"]+)"', cv_data)
        if cn_match and jp_match:
            result[int(cv_id)] = f"{cn_match.group(1)}/{jp_match.group(1)}"
    return result


def parse_level_up_file(file_path):
    """解析 BaseCardLevelUp.lua，返回等级属性增量。"""
    result = {}
    attr_map = {"40000102": "生命", "40000103": "攻击", "40000104": "防御"}
    content = file_path.read_text(encoding="utf-8") if hasattr(file_path, "read_text") else open(file_path, encoding="utf-8").read()
    pattern = r'\[(\d+)\] = (\{(?:[^{}]|(?:\{(?:[^{}]|(?:\{[^{}]*\}))*\}))*\})'
    for level_id, level_data in re.findall(pattern, content, re.DOTALL):
        attr_match = re.search(r'add_attr = \{(.*?)\}', level_data, re.DOTALL)
        if not attr_match:
            continue
        attrs = {}
        for item in re.findall(r'"([^]"]+)"', attr_match.group(1)):
            parts = item.split(':')
            if len(parts) == 3:
                name = attr_map.get(parts[1], parts[1])
                attrs[name] = attrs.get(name, 0) + int(parts[2])
        if attrs:
            result[int(level_id)] = attrs
    return result


def parse_badge_suit_file(file_path, word_dict):
    """解析 BaseBadgeSuitGroup.lua，返回徽章套装名称。"""
    result = {}
    content = file_path.read_text(encoding="utf-8") if hasattr(file_path, "read_text") else open(file_path, encoding="utf-8").read()
    pattern = r'\[(\d+)\] = \{(.*?)\}(?=,|\n\s*\]|\n\s*\})'
    for suit_id, suit_data in re.findall(pattern, content, re.DOTALL):
        name_match = re.search(r'name = function\(\)\s*return T\((\d+)\)\s*end', suit_data)
        if name_match:
            key = int(name_match.group(1))
            result[int(suit_id)] = word_dict.get(key, str(suit_id))
    return result


def parse_item_file(file_path, word_dict):
    """解析 BaseItem.lua，返回物品名称。"""
    result = {}
    content = file_path.read_text(encoding="utf-8") if hasattr(file_path, "read_text") else open(file_path, encoding="utf-8").read()
    pattern = r'\[(\d+)\] = \{(.*?)\}(?=,|\n\s*\]|\n\s*\})'
    for item_id, item_data in re.findall(pattern, content, re.DOTALL):
        name_match = re.search(r'name = function\(\)\s*return T\((\d+)\)\s*end', item_data)
        if name_match:
            key = int(name_match.group(1))
            result[int(item_id)] = word_dict.get(key, str(item_id))
    return result
