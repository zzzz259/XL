# -*- coding: utf-8 -*-
"""角色突破、技能升级及消耗表解析。"""

import re


def _read(file_path):
    with open(file_path, 'r', encoding='utf-8') as handle:
        return handle.read()


def parse_quality_up_file_with_cost(file_path):
    """解析 BaseCardQualityUp.lua，返回带消耗的字典。"""
    result = {}
    attr_map = {"40000102": "生命", "40000103": "攻击", "40000104": "防御"}
    content = _read(file_path)
    pattern = r'\[(\d+)\] = (\{(?:[^{}]|(?:\{(?:[^{}]|(?:\{[^{}]*\}))*\}))*\})'
    for quality_id, quality_data in re.findall(pattern, content, re.DOTALL):
        attrs = {}
        attr_match = re.search(r'add_attr = \{(.*?)\}', quality_data, re.DOTALL)
        if attr_match:
            for item in re.findall(r'"([^]"]+)"', attr_match.group(1)):
                parts = item.split(':')
                if len(parts) == 3:
                    name = attr_map.get(parts[1], parts[1])
                    attrs[name] = attrs.get(name, 0) + int(parts[2])
        costs = []
        cost_match = re.search(r'cost\s*=\s*\{([^}]*)\}', quality_data, re.DOTALL)
        if cost_match:
            for item in re.findall(r'"([^]"]+)"', cost_match.group(1)):
                parts = item.split(':')
                if len(parts) == 3:
                    costs.append((int(parts[1]), int(parts[2])))
        result[int(quality_id)] = {"add_attr": attrs, "cost": costs}
    return result


def parse_skill_level_up_file_with_cost(file_path):
    """解析 BaseSkillLevelUp.lua，返回技能升级消耗字典。"""
    result = {}
    content = _read(file_path)
    pattern = r'\[(\d+)\] = (\{(?:[^{}]|(?:\{(?:[^{}]|(?:\{[^{}]*\}))*\}))*\})'
    for skill_level_id, skill_level_data in re.findall(pattern, content, re.DOTALL):
        costs = []
        cost_match = re.search(r'cost\s*=\s*\{([^}]*)\}', skill_level_data, re.DOTALL)
        if cost_match:
            for item in re.findall(r'"([^]"]+)"', cost_match.group(1)):
                parts = item.split(':')
                if len(parts) == 3:
                    costs.append((int(parts[1]), int(parts[2])))
        result[int(skill_level_id)] = costs
    return result


def get_breakthrough_cost(char_id, quality_up_dict, item_dict):
    """获取角色突破消耗。"""
    costs = ["", "", "", ""]
    for index in range(4):
        quality_id = char_id * 1000 + index
        if quality_id in quality_up_dict:
            values = []
            for item_id, item_count in quality_up_dict[quality_id].get("cost", []):
                values.append(f"{item_dict.get(item_id, f'未知物品({item_id})')} * {item_count}")
            if values:
                costs[index] = " | ".join(values)
    return costs


def get_normal_skill_upgrade_cost(normal_skill_id, skill_level_up_dict, item_dict):
    """获取普通技能升级消耗。"""
    costs = ["", "", ""]
    for index in range(2, 5):
        skill_level_id = normal_skill_id * 1000 + index
        if skill_level_id in skill_level_up_dict:
            values = [f"{item_dict.get(item_id, f'未知物品({item_id})')} * {item_count}" for item_id, item_count in skill_level_up_dict[skill_level_id]]
            if values:
                costs[index - 2] = " | ".join(values)
    return costs


def get_passive_skill_upgrade_cost(passive_skill_id, skill_level_up_dict, item_dict):
    """获取被动技能升级消耗。"""
    costs = ["", "", ""]
    for index in range(1, 4):
        skill_level_id = passive_skill_id * 1000 + index
        if skill_level_id in skill_level_up_dict:
            values = [f"{item_dict.get(item_id, f'未知物品({item_id})')} * {item_count}" for item_id, item_count in skill_level_up_dict[skill_level_id]]
            if values:
                costs[index - 1] = " | ".join(values)
    return costs
