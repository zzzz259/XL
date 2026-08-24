# -*- coding: utf-8 -*-
"""技能名称、描述、等级参数与觉醒信息解析。"""

import re

from app.platform.diagnostics import logger

from .common import (
    merge_t_function_params,
    parse_t_function_params,
    process_t_function_params,
)


def _read(file_path):
    with open(file_path, 'r', encoding='utf-8') as handle:
        return handle.read()


def _clean_text(text):
    text = text.replace("%%", "%").replace("\\n", "\n")
    text = re.sub(r'\[color=#[0-9a-fA-F]+\]', '', text)
    text = re.sub(r'\[/color\]', '', text)
    return re.sub(r'\[[^\]]+\]', '', text)


def _resolve_description(processed_params, word_dict):
    if not processed_params:
        return None
    try:
        main_id = int(processed_params[0])
    except ValueError:
        return f"参数错误: {processed_params[0]}"
    main_text = word_dict.get(main_id, f"未知({main_id})")
    for param in processed_params[1:]:
        if "%s" in main_text:
            main_text = main_text.replace("%s", str(param), 1)
        elif "%d" in main_text:
            main_text = main_text.replace("%d", str(param), 1)
    return _clean_text(main_text)


def extract_skill_info(skill_id, skill_file_path, skill_level_up_file_path, word_dict):
    """提取单等级技能信息。"""
    skill_name = "未知"
    skill_description = "未知"
    try:
        skill_content = _read(skill_file_path)
        skill_match = re.search(rf'\[{skill_id}\] = \{{(.*?)\}}', skill_content, re.DOTALL)
        if skill_match:
            name_match = re.search(r'name = function\(\)\s*return T\((\d+)\)\s*end', skill_match.group(1))
            if name_match:
                name_key = int(name_match.group(1))
                skill_name = word_dict.get(name_key, f"未知({name_key})")
            level_content = _read(skill_level_up_file_path)
            level_id = skill_id * 1000 + 1
            level_match = re.search(rf'\[{level_id}\] = \{{(.*?)\}}', level_content, re.DOTALL)
            if level_match:
                des_match = re.search(r'des\s*=\s*function\(\)\s*return\s*T\((.*?)\)\s*end', level_match.group(1), re.DOTALL)
                if des_match:
                    params = process_t_function_params(parse_t_function_params(des_match.group(1)), word_dict)
                    description = _resolve_description(params, word_dict)
                    if description is not None:
                        skill_description = description
    except Exception:
        logger.debug(f"提取技能信息出错: skill_id={skill_id}")
    return f"{skill_name}\n{skill_description}"


def extract_awakening_skill_info(skill_id, skill_file_path, skill_level_up_file_path, word_dict):
    """提取觉醒技能信息。"""
    skill_name = "未知"
    skill_description = "未知"
    try:
        skill_content = _read(skill_file_path)
        skill_match = re.search(rf'\[{skill_id}\] = \{{(.*?)\}}', skill_content, re.DOTALL)
        if skill_match:
            name_match = re.search(r'name = function\(\)\s*return T\((\d+)\)\s*end', skill_match.group(1))
            if name_match:
                name_key = int(name_match.group(1))
                skill_name = word_dict.get(name_key, f"未知({name_key})")
            level_content = _read(skill_level_up_file_path)
            level_id = skill_id * 1000 + 1
            level_match = re.search(rf'\[{level_id}\] = \{{(.*?)\}}', level_content, re.DOTALL)
            if level_match:
                des_match = re.search(r'des\s*=\s*function\(\)\s*return\s*T\((.*?)\)\s*end', level_match.group(1), re.DOTALL)
                if des_match:
                    params = process_t_function_params(parse_t_function_params(des_match.group(1)), word_dict)
                    description = _resolve_description(params, word_dict)
                    if description is not None:
                        skill_description = description
    except Exception:
        logger.debug(f"提取觉醒技能信息出错: skill_id={skill_id}")
    return f"{skill_name}\n{skill_description}"


def extract_awakening_info(association_skill_id, skill_level_up_file_path, word_dict):
    """提取觉醒描述信息。"""
    awakening_info = ""
    awakening_mapping = {14: "觉醒1", 15: "觉醒2", 16: "觉醒3", 17: "觉醒4", 18: "觉醒5"}
    try:
        content = _read(skill_level_up_file_path)
        match = re.search(rf'(\[{association_skill_id}\]\s*=\s*\{{.*?association_des.*?\n\s*\}})', content, re.DOTALL)
        if match:
            des_match = re.search(r'association_des\s*=\s*function\(\)\s*return\s*T\((.*?)\)\s*end', match.group(1), re.DOTALL)
            if des_match:
                params = process_t_function_params(parse_t_function_params(des_match.group(1)), word_dict)
                description = _resolve_description(params, word_dict)
                if description is not None:
                    level = int(str(association_skill_id)[3:5])
                    name = awakening_mapping.get(level, f"觉醒{level - 13}")
                    awakening_info = f"\n\n{name}\n{description}"
    except Exception:
        logger.debug(f"提取觉醒信息出错: association_skill_id={association_skill_id}")
    return awakening_info


def extract_multi_level_skill_info_new(skill_id, skill_file_path, skill_level_up_file_path, word_dict,
                                       max_level_override=None, is_burst_skill=False):
    """提取多等级技能信息（在参数阶段合并）。"""
    skill_name = "未知"
    skill_type = 0
    skill_cd = ""
    all_params = []
    skill_match = None
    try:
        skill_content = _read(skill_file_path)
        skill_match = re.search(rf'\[{skill_id}\] = \{{(.*?)\}}', skill_content, re.DOTALL)
        if skill_match:
            skill_data = skill_match.group(1)
            name_match = re.search(r'name = function\(\)\s*return T\((\d+)\)\s*end', skill_data)
            if name_match:
                skill_name = word_dict.get(int(name_match.group(1)), f"未知({name_match.group(1)})")
            if is_burst_skill:
                cd_match = re.search(r'cd\s*=\s*(\d+)', skill_data)
                if cd_match:
                    skill_cd = cd_match.group(1) + "秒"
                    skill_name = f"{skill_name} {skill_cd}"
            type_match = re.search(r'type\s*=\s*(\d+)', skill_data)
            if type_match:
                skill_type = int(type_match.group(1))
            if max_level_override is not None:
                max_level = max_level_override
            elif skill_type == 1:
                max_level = 4
            elif skill_type in [2, 7]:
                max_level = 6
            else:
                max_match = re.search(r'max_level\s*=\s*(\d+)', skill_data)
                max_level = int(max_match.group(1)) if max_match else 1
            level_content = _read(skill_level_up_file_path)
            for level in range(1, max_level + 1):
                level_id = skill_id * 1000 + level
                level_match = re.search(rf'\[{level_id}\] = \{{(.*?)\}}', level_content, re.DOTALL)
                if level_match:
                    des_match = re.search(r'des\s*=\s*function\(\)\s*return\s*T\((.*?)\)\s*end', level_match.group(1), re.DOTALL)
                    if des_match:
                        params = parse_t_function_params(des_match.group(1))
                        all_params.append(process_t_function_params(params, word_dict))
    except Exception:
        logger.debug(f"提取多等级技能信息出错: skill_id={skill_id}")
        return f"{skill_name}\n未知"

    awakening_info = ""
    try:
        if skill_match:
            association_match = re.search(r'association_skills\s*=\s*\{\s*"([^"]+)"', skill_match.group(1), re.DOTALL)
            if association_match:
                parts = association_match.group(1).split(':')
                if len(parts) == 3:
                    awakening_info = extract_awakening_info(int(parts[1]), skill_level_up_file_path, word_dict)
    except Exception:
        logger.debug(f"提取觉醒技能信息出错: skill_id={skill_id}")

    if not all_params:
        return f"{skill_name}\n未知{awakening_info}"
    merged_params = merge_t_function_params(all_params)
    try:
        main_id = int(merged_params[0])
        main_text = word_dict.get(main_id, f"未知({main_id})")
        processed_params = []
        for param in merged_params[1:]:
            if isinstance(param, str) and param.startswith('T(') and param.endswith(')'):
                nested = process_t_function_params(parse_t_function_params(param[2:-1]), word_dict)
                if nested:
                    try:
                        nested_text = word_dict.get(int(nested[0]), f"未知({nested[0]})")
                        for nested_param in nested[1:]:
                            if "%s" in nested_text:
                                nested_text = nested_text.replace("%s", str(nested_param), 1)
                            elif "%d" in nested_text:
                                nested_text = nested_text.replace("%d", str(nested_param), 1)
                        processed_params.append(nested_text.replace("%%", "%"))
                    except ValueError:
                        processed_params.append(f"参数错误: {nested[0]}")
            else:
                processed_params.append(param)
        for param in processed_params:
            if "%s" in main_text:
                main_text = main_text.replace("%s", str(param), 1)
            elif "%d" in main_text:
                main_text = main_text.replace("%d", str(param), 1)
        main_text = _clean_text(main_text)
        return f"{skill_name}\n{main_text}{awakening_info}"
    except Exception:
        logger.debug(f"处理合并参数出错: skill_id={skill_id}")
        return f"{skill_name}\n未知{awakening_info}"
