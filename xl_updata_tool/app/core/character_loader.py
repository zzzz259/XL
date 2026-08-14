# -*- coding: utf-8 -*-
"""模块职责：角色 Lua 数据解析引擎

将 BaseCard.lua / BaseWord_cn.lua / BaseSkill.lua 等 Lua 配置文件解析为结构化角色数据。
所有函数均为模块级函数，不依赖 MainWindow。需要访问 word_map 时通过参数传入。
"""

import os
import re

from app.core.logger import logger


# 元素（属性）映射
ELEMENT_MAP = {1: "水属性", 2: "火属性", 3: "木属性", 4: "暗属性", 5: "光属性"}


# ---------------------------------------------------------------------------
# 大括号/块提取
# ---------------------------------------------------------------------------

def extract_all_card_blocks(card_content):
    """解析 BaseCard.lua 中所有 `[数字] = { ... }` 块，返回 (raw_id, block_text) 列表。
    使用栈计数法处理嵌套大括号，确保正确提取完整块内容。
    """
    blocks = []
    pattern = re.compile(r'\[\s*(\d+)\s*\]\s*=\s*\{')
    for m in pattern.finditer(card_content):
        raw_id = int(m.group(1))
        start = m.end() - 1  # 指向 {
        depth = 1
        pos = start + 1
        while pos < len(card_content) and depth > 0:
            ch = card_content[pos]
            if ch == '{':
                depth += 1
            elif ch == '}':
                depth -= 1
            pos += 1
        if depth == 0:
            block_text = card_content[m.start():pos]
            blocks.append((raw_id, block_text))
    return blocks


def extract_t_references(block_text):
    """从块文本中提取所有 T(数字) 引用的 raw_id 集合"""
    return set(int(x) for x in re.findall(r'T\((\d+)\)', block_text))


def parse_skill_up_args(args_str):
    """解析 BaseSkillLevelUp.lua 中 des 的 T() 参数列表。
    输入: "80512141, T(80520017, 70), T(80520012, 1), T(80520018, 7)"
    输出: [80512141, 70, 1, 7]  (第一个是模板 ID，后续是数值)
    """
    result = []
    # 先处理嵌套 T(id, value) 提取 value
    def replace_t(m):
        parts = [int(x) for x in re.findall(r'\d+', m.group(1))]
        if len(parts) >= 2:
            return str(parts[1])
        return "0"
    processed = re.sub(r'T\(([^)]*)\)', replace_t, args_str)
    # 现在提取所有数字
    for num_str in re.findall(r'\d+', processed):
        result.append(int(num_str))
    return result


# ---------------------------------------------------------------------------
# T() 参数解析
# ---------------------------------------------------------------------------

def parse_t_args(s):
    """解析 T() 内部的参数列表，处理嵌套 T() 中的逗号。"""
    args = []
    depth = 0
    current = []
    for ch in s:
        if ch == '(':
            depth += 1
            current.append(ch)
        elif ch == ')':
            depth -= 1
            current.append(ch)
        elif ch == ',' and depth == 0:
            args.append(''.join(current).strip())
            current = []
        else:
            current.append(ch)
    if current:
        args.append(''.join(current).strip())
    return args


def resolve_t_call(call_str, word_map):
    """递归解析 T(id, arg1, arg2, ...) 格式的调用，返回最终字符串。
    从 word_map 获取模板文本，用参数替换 %s/%d 占位符。
    嵌套的 T() 会递归解析。
    """
    call_str = call_str.strip()
    # 如果是纯数字，直接返回
    try:
        int(call_str)
        return call_str
    except ValueError:
        pass
    # 解析参数列表
    args = parse_t_args(call_str)
    if not args:
        return call_str
    # 第一个参数是模板 ID
    try:
        template_id = int(args[0].strip())
    except ValueError:
        return call_str
    # 获取模板文本
    template = word_map.get(template_id, "")
    if not template:
        logger.debug(f"resolve_t_call: 模板缺失 template_id={template_id}")
        return str(template_id)
    # 处理剩余参数
    values = []
    for arg in args[1:]:
        arg = arg.strip()
        if arg.startswith('T(') and arg.endswith(')'):
            inner = arg[2:-1]  # 去掉 T( 和 )
            resolved = resolve_t_call(inner, word_map)
            values.append(resolved)
        else:
            values.append(arg)
    # 格式化模板：尝试 % 格式化，失败则回退到简单替换
    typed_values = []
    for v in values:
        try:
            if '.' in v:
                typed_values.append(float(v))
            else:
                typed_values.append(int(v))
        except (ValueError, TypeError):
            typed_values.append(v)
    try:
        result = template % tuple(typed_values)
    except (TypeError, ValueError, IndexError):
        result = template
        for v in values:
            result = result.replace('%s', str(v), 1)
            result = result.replace('%d', str(v), 1)
    return result


def parse_t_function_params(content):
    """解析T函数参数"""
    params = []
    current_param = ""
    bracket_count = 0
    in_quotes = False
    for char in content:
        if char == '"' and not in_quotes:
            in_quotes = True
        elif char == '"' and in_quotes:
            in_quotes = False
        elif char == '(' and not in_quotes:
            bracket_count += 1
        elif char == ')' and not in_quotes:
            bracket_count -= 1
        elif char == ',' and bracket_count == 0 and not in_quotes:
            params.append(current_param.strip())
            current_param = ""
            continue
        current_param += char
    if current_param:
        params.append(current_param.strip())
    return params


def process_t_function_params(params, word_dict):
    """处理T函数参数（嵌套处理）"""
    processed_params = []
    for param in params:
        if isinstance(param, str) and param.startswith('T(') and param.endswith(')'):
            nested_content = param[2:-1]
            nested_parts = parse_t_function_params(nested_content)
            nested_processed = process_t_function_params(nested_parts, word_dict)
            if nested_processed:
                try:
                    nested_id = int(nested_processed[0])
                    nested_text = word_dict.get(nested_id, f"未知({nested_id})")
                    for i, nested_param in enumerate(nested_processed[1:]):
                        if "%s" in nested_text:
                            nested_text = nested_text.replace("%s", str(nested_param), 1)
                        elif "%d" in nested_text:
                            nested_text = nested_text.replace("%d", str(nested_param), 1)
                    nested_text = nested_text.replace("%%", "%")
                    processed_params.append(nested_text)
                except ValueError:
                    processed_params.append(f"参数错误: {nested_processed[0]}")
            else:
                processed_params.append(param)
        else:
            processed_params.append(param)
    return processed_params


def merge_t_function_params(all_params):
    """合并多级T函数参数"""
    if not all_params:
        return []
    if not all(len(params) == len(all_params[0]) for params in all_params):
        return all_params[0]
    merged_params = []
    for i in range(len(all_params[0])):
        values = [params[i] for params in all_params]
        all_have_percent = True
        for v in values:
            if '%' not in str(v):
                all_have_percent = False
                break
        if all_have_percent:
            numeric_parts = []
            for v in values:
                v_str = str(v)
                if '%' in v_str:
                    percent_index = v_str.index('%')
                    numeric_part = v_str[:percent_index]
                    numeric_parts.append(numeric_part)
                else:
                    numeric_parts.append(v_str)
            if all(n == numeric_parts[0] for n in numeric_parts):
                merged_params.append(f"{numeric_parts[0]}%")
            else:
                merged_params.append("/".join(numeric_parts) + "%")
        else:
            all_numeric = True
            for v in values:
                if not re.match(r'^-?\d+(\.\d+)?$', str(v)):
                    all_numeric = False
                    break
            if all_numeric:
                if all(v == values[0] for v in values):
                    merged_params.append(values[0])
                else:
                    merged_params.append("/".join(map(str, values)))
            else:
                if all(v == values[0] for v in values):
                    merged_params.append(values[0])
                else:
                    merged_params.append("/".join(map(str, values)))
    return merged_params


# ---------------------------------------------------------------------------
# 各种 Lua 文件解析
# ---------------------------------------------------------------------------

def parse_word_file(file_path):
    """解析 BaseWord_cn.lua，返回 word_dict (id->文本)"""
    d = {}
    with open(file_path, 'r', encoding='utf-8') as f:
        content = f.read()
    # 使用完整模式匹配
    pattern = r'\[(\d+)\] = \{(.*?)\}(?=,|\n\s*\]|\n\s*\})'
    matches = re.findall(pattern, content, re.DOTALL)
    for key_id, item_content in matches:
        key_id = int(key_id)
        name_pattern = r'(?:name|sub_name) = "([^"]+)"'
        name_match = re.search(name_pattern, item_content)
        if name_match:
            d[key_id] = name_match.group(1)
    # 也匹配直接赋值的文本 [id] = "文本"
    for m in re.finditer(r'\[\s*(\d+)\s*\]\s*=\s*"([^"]*)"', content):
        d[int(m.group(1))] = m.group(2)
    return d


def parse_cv_file(file_path):
    """解析 BaseCvNameCn.lua，返回 cv_dict (id->"中文名/日文名")"""
    d = {}
    with open(file_path, 'r', encoding='utf-8') as f:
        content = f.read()
    pattern = r'\[(\d+)\] = \{(.*?)\}(?=,|\n\s*\]|\n\s*\})'
    matches = re.findall(pattern, content, re.DOTALL)
    for match in matches:
        cv_id, cv_data = match
        cv_id = int(cv_id)
        cn_pattern = r'name_cn = "([^"]+)"'
        cn_match = re.search(cn_pattern, cv_data)
        jp_pattern = r'name_jp = "([^"]+)"'
        jp_match = re.search(jp_pattern, cv_data)
        if cn_match and jp_match:
            cn_name = cn_match.group(1)
            jp_name = jp_match.group(1)
            d[cv_id] = f"{cn_name}/{jp_name}"
    return d


def parse_level_up_file(file_path):
    """解析 BaseCardLevelUp.lua，返回 level_up_dict (id->{属性名:增加值})"""
    d = {}
    attr_map = {"40000102": "生命", "40000103": "攻击", "40000104": "防御"}
    with open(file_path, 'r', encoding='utf-8') as f:
        content = f.read()
    pattern = r'\[(\d+)\] = (\{(?:[^{}]|(?:\{(?:[^{}]|(?:\{[^{}]*\}))*\}))*\})'
    matches = re.findall(pattern, content, re.DOTALL)
    for match in matches:
        level_id, level_data = match
        level_id = int(level_id)
        attr_pattern = r'add_attr = \{(.*?)\}'
        attr_match = re.search(attr_pattern, level_data, re.DOTALL)
        if attr_match:
            attr_content = attr_match.group(1)
            attr_items = re.findall(r'"([^]"]+)"', attr_content)
            attr_dict = {}
            for item in attr_items:
                parts = item.split(':')
                if len(parts) == 3:
                    atype = parts[1]
                    aval = int(parts[2])
                    aname = attr_map.get(atype, atype)
                    attr_dict[aname] = attr_dict.get(aname, 0) + aval
            if attr_dict:
                d[level_id] = attr_dict
    return d


def parse_badge_suit_file(file_path, word_dict):
    """解析 BaseBadgeSuitGroup.lua，返回 badge_suit_dict (id->名称)"""
    d = {}
    with open(file_path, 'r', encoding='utf-8') as f:
        content = f.read()
    pattern = r'\[(\d+)\] = \{(.*?)\}(?=,|\n\s*\]|\n\s*\})'
    matches = re.findall(pattern, content, re.DOTALL)
    for match in matches:
        suit_id, suit_data = match
        suit_id = int(suit_id)
        name_pattern = r'name = function\(\)\s*return T\((\d+)\)\s*end'
        name_match = re.search(name_pattern, suit_data)
        if name_match:
            name_key = int(name_match.group(1))
            suit_name = word_dict.get(name_key, str(suit_id))
            d[suit_id] = suit_name
    return d


def parse_item_file(file_path, word_dict):
    """解析 BaseItem.lua，返回 item_dict (id->名称)"""
    d = {}
    with open(file_path, 'r', encoding='utf-8') as f:
        content = f.read()
    pattern = r'\[(\d+)\] = \{(.*?)\}(?=,|\n\s*\]|\n\s*\})'
    matches = re.findall(pattern, content, re.DOTALL)
    for match in matches:
        item_id, item_data = match
        item_id = int(item_id)
        name_pattern = r'name = function\(\)\s*return T\((\d+)\)\s*end'
        name_match = re.search(name_pattern, item_data)
        if name_match:
            name_key = int(name_match.group(1))
            item_name = word_dict.get(name_key, str(item_id))
            d[item_id] = item_name
    return d


def parse_quality_up_file_with_cost(file_path):
    """解析 BaseCardQualityUp.lua，返回带消耗的字典"""
    quality_up_dict = {}
    attr_map = {"40000102": "生命", "40000103": "攻击", "40000104": "防御"}
    with open(file_path, 'r', encoding='utf-8') as f:
        content = f.read()
    pattern = r'\[(\d+)\] = (\{(?:[^{}]|(?:\{(?:[^{}]|(?:\{[^{}]*\}))*\}))*\})'
    matches = re.findall(pattern, content, re.DOTALL)
    for match in matches:
        quality_id, quality_data = match
        quality_id = int(quality_id)
        attr_dict = {}
        am = re.search(r'add_attr = \{(.*?)\}', quality_data, re.DOTALL)
        if am:
            for item in re.findall(r'"([^]"]+)"', am.group(1)):
                parts = item.split(':')
                if len(parts) == 3:
                    atype = parts[1]
                    aval = int(parts[2])
                    aname = attr_map.get(atype, atype)
                    attr_dict[aname] = attr_dict.get(aname, 0) + aval
        cost_list = []
        cm = re.search(r'cost\s*=\s*\{([^}]*)\}', quality_data, re.DOTALL)
        if cm:
            for item in re.findall(r'"([^]"]+)"', cm.group(1)):
                parts = item.split(':')
                if len(parts) == 3:
                    item_id = int(parts[1])
                    item_count = int(parts[2])
                    cost_list.append((item_id, item_count))
        quality_up_dict[quality_id] = {"add_attr": attr_dict, "cost": cost_list}
    return quality_up_dict


def parse_skill_level_up_file_with_cost(file_path):
    """解析BaseSkillLevelUp.lua，返回技能升级消耗字典"""
    skill_level_up_dict = {}
    with open(file_path, 'r', encoding='utf-8') as f:
        content = f.read()
    pattern = r'\[(\d+)\] = (\{(?:[^{}]|(?:\{(?:[^{}]|(?:\{[^{}]*\}))*\}))*\})'
    matches = re.findall(pattern, content, re.DOTALL)
    for match in matches:
        skill_level_id, skill_level_data = match
        skill_level_id = int(skill_level_id)
        cost_pattern = r'cost\s*=\s*\{([^}]*)\}'
        cost_match = re.search(cost_pattern, skill_level_data, re.DOTALL)
        cost_list = []
        if cost_match:
            cost_content = cost_match.group(1)
            cost_items = re.findall(r'"([^]"]+)"', cost_content)
            for item in cost_items:
                parts = item.split(':')
                if len(parts) == 3:
                    item_id = int(parts[1])
                    item_count = int(parts[2])
                    cost_list.append((item_id, item_count))
        skill_level_up_dict[skill_level_id] = cost_list
    return skill_level_up_dict


# ---------------------------------------------------------------------------
# 技能信息提取
# ---------------------------------------------------------------------------

def extract_skill_info(skill_id, skill_file_path, skill_level_up_file_path, word_dict):
    """提取单等级技能信息"""
    skill_name = "未知"
    skill_description = "未知"
    try:
        with open(skill_file_path, 'r', encoding='utf-8') as f:
            skill_content = f.read()
        skill_pattern = rf'\[{skill_id}\] = \{{(.*?)\}}'
        skill_match = re.search(skill_pattern, skill_content, re.DOTALL)
        if skill_match:
            skill_data = skill_match.group(1)
            name_pattern = r'name = function\(\)\s*return T\((\d+)\)\s*end'
            name_match = re.search(name_pattern, skill_data)
            if name_match:
                name_key = int(name_match.group(1))
                skill_name = word_dict.get(name_key, f"未知({name_key})")
            with open(skill_level_up_file_path, 'r', encoding='utf-8') as f:
                skill_level_up_content = f.read()
            skill_level_id = skill_id * 1000 + 1
            skill_level_pattern = rf'\[{skill_level_id}\] = \{{(.*?)\}}'
            skill_level_match = re.search(skill_level_pattern, skill_level_up_content, re.DOTALL)
            if skill_level_match:
                skill_level_data = skill_level_match.group(1)
                des_pattern = r'des\s*=\s*function\(\)\s*return\s*T\((.*?)\)\s*end'
                des_match = re.search(des_pattern, skill_level_data, re.DOTALL)
                if des_match:
                    des_content = des_match.group(1)
                    params = parse_t_function_params(des_content)
                    processed_params = process_t_function_params(params, word_dict)
                    if processed_params:
                        try:
                            main_id = int(processed_params[0])
                            main_text = word_dict.get(main_id, f"未知({main_id})")
                            for i, param in enumerate(processed_params[1:]):
                                if "%s" in main_text:
                                    main_text = main_text.replace("%s", str(param), 1)
                            main_text = main_text.replace("%%", "%")
                            main_text = main_text.replace("\\n", "\n")
                            main_text = re.sub(r'\[color=#[0-9a-fA-F]+\]', '', main_text)
                            main_text = re.sub(r'\[/color\]', '', main_text)
                            main_text = re.sub(r'\[[^\]]+\]', '', main_text)
                            skill_description = main_text
                        except ValueError:
                            skill_description = f"参数错误: {processed_params[0]}"
    except Exception:
        logger.debug(f"提取技能信息出错: skill_id={skill_id}")
    return f"{skill_name}\n{skill_description}"


def extract_awakening_skill_info(skill_id, skill_file_path, skill_level_up_file_path, word_dict):
    """提取觉醒技能信息"""
    skill_name = "未知"
    skill_description = "未知"
    try:
        with open(skill_file_path, 'r', encoding='utf-8') as f:
            skill_content = f.read()
        skill_pattern = rf'\[{skill_id}\] = \{{(.*?)\}}'
        skill_match = re.search(skill_pattern, skill_content, re.DOTALL)
        if skill_match:
            skill_data = skill_match.group(1)
            name_pattern = r'name = function\(\)\s*return T\((\d+)\)\s*end'
            name_match = re.search(name_pattern, skill_data)
            if name_match:
                name_key = int(name_match.group(1))
                skill_name = word_dict.get(name_key, f"未知({name_key})")
            with open(skill_level_up_file_path, 'r', encoding='utf-8') as f:
                skill_level_up_content = f.read()
            skill_level_id = skill_id * 1000 + 1
            skill_level_pattern = rf'\[{skill_level_id}\] = \{{(.*?)\}}'
            skill_level_match = re.search(skill_level_pattern, skill_level_up_content, re.DOTALL)
            if skill_level_match:
                skill_level_data = skill_level_match.group(1)
                des_pattern = r'des\s*=\s*function\(\)\s*return\s*T\((.*?)\)\s*end'
                des_match = re.search(des_pattern, skill_level_data, re.DOTALL)
                if des_match:
                    des_content = des_match.group(1)
                    params = parse_t_function_params(des_content)
                    processed_params = process_t_function_params(params, word_dict)
                    if processed_params:
                        try:
                            main_id = int(processed_params[0])
                            main_text = word_dict.get(main_id, f"未知({main_id})")
                            for i, param in enumerate(processed_params[1:]):
                                if "%s" in main_text:
                                    main_text = main_text.replace("%s", str(param), 1)
                            main_text = main_text.replace("%%", "%")
                            main_text = main_text.replace("\\n", "\n")
                            main_text = re.sub(r'\[color=#[0-9a-fA-F]+\]', '', main_text)
                            main_text = re.sub(r'\[/color\]', '', main_text)
                            main_text = re.sub(r'\[[^\]]+\]', '', main_text)
                            skill_description = main_text
                        except ValueError:
                            skill_description = f"参数错误: {processed_params[0]}"
    except Exception:
        logger.debug(f"提取觉醒技能信息出错: skill_id={skill_id}")
    return f"{skill_name}\n{skill_description}"


def extract_awakening_info(association_skill_id, skill_level_up_file_path, word_dict):
    """提取觉醒描述信息"""
    awakening_info = ""
    AWAKENING_MAPPING = {14: "觉醒1", 15: "觉醒2", 16: "觉醒3", 17: "觉醒4", 18: "觉醒5"}
    try:
        with open(skill_level_up_file_path, 'r', encoding='utf-8') as f:
            skill_level_up_content = f.read()
        skill_level_pattern = rf'(\[{association_skill_id}\]\s*=\s*\{{.*?association_des.*?\n\s*\}})'
        skill_level_match = re.search(skill_level_pattern, skill_level_up_content, re.DOTALL)
        if skill_level_match:
            skill_level_data = skill_level_match.group(1)
            association_des_pattern = r'association_des\s*=\s*function\(\)\s*return\s*T\((.*?)\)\s*end'
            association_des_match = re.search(association_des_pattern, skill_level_data, re.DOTALL)
            if association_des_match:
                association_des_content = association_des_match.group(1)
                params = parse_t_function_params(association_des_content)
                processed_params = process_t_function_params(params, word_dict)
                if processed_params:
                    try:
                        main_id = int(processed_params[0])
                        main_text = word_dict.get(main_id, f"未知({main_id})")
                        for i, param in enumerate(processed_params[1:]):
                            if "%s" in main_text:
                                main_text = main_text.replace("%s", str(param), 1)
                        main_text = main_text.replace("%%", "%")
                        main_text = main_text.replace("\\n", "\n")
                        main_text = re.sub(r'\[color=#[0-9a-fA-F]+\]', '', main_text)
                        main_text = re.sub(r'\[/color\]', '', main_text)
                        main_text = re.sub(r'\[[^\]]+\]', '', main_text)
                        awakening_level = int(str(association_skill_id)[3:5])
                        awakening_name = AWAKENING_MAPPING.get(awakening_level, f"觉醒{awakening_level - 13}")
                        awakening_info = f"\n\n{awakening_name}\n{main_text}"
                    except ValueError:
                        awakening_info = f"\n\n觉醒描述参数错误"
    except Exception:
        logger.debug(f"提取觉醒信息出错: association_skill_id={association_skill_id}")
    return awakening_info


def extract_multi_level_skill_info_new(skill_id, skill_file_path, skill_level_up_file_path, word_dict,
                                       max_level_override=None, is_burst_skill=False):
    """提取多等级技能信息（在参数阶段合并）"""
    skill_name = "未知"
    skill_type = 0
    skill_cd = ""
    all_params = []
    skill_match = None
    try:
        with open(skill_file_path, 'r', encoding='utf-8') as f:
            skill_content = f.read()
        skill_pattern = rf'\[{skill_id}\] = \{{(.*?)\}}'
        skill_match = re.search(skill_pattern, skill_content, re.DOTALL)
        if skill_match:
            skill_data = skill_match.group(1)
            name_pattern = r'name = function\(\)\s*return T\((\d+)\)\s*end'
            name_match = re.search(name_pattern, skill_data)
            if name_match:
                name_key = int(name_match.group(1))
                skill_name = word_dict.get(name_key, f"未知({name_key})")
            if is_burst_skill:
                cd_pattern = r'cd\s*=\s*(\d+)'
                cd_match = re.search(cd_pattern, skill_data)
                if cd_match:
                    skill_cd = cd_match.group(1) + "秒"
                    skill_name = f"{skill_name} {skill_cd}"
            type_pattern = r'type\s*=\s*(\d+)'
            type_match = re.search(type_pattern, skill_data)
            if type_match:
                skill_type = int(type_match.group(1))
            max_level = 1
            if max_level_override is not None:
                max_level = max_level_override
            elif skill_type == 1:
                max_level = 4
            elif skill_type in [2, 7]:
                max_level = 6
            else:
                max_level_pattern = r'max_level\s*=\s*(\d+)'
                max_level_match = re.search(max_level_pattern, skill_data)
                if max_level_match:
                    max_level = int(max_level_match.group(1))
            with open(skill_level_up_file_path, 'r', encoding='utf-8') as f:
                skill_level_up_content = f.read()
            for level in range(1, max_level + 1):
                skill_level_id = skill_id * 1000 + level
                skill_level_pattern = rf'\[{skill_level_id}\] = \{{(.*?)\}}'
                skill_level_match = re.search(skill_level_pattern, skill_level_up_content, re.DOTALL)
                if skill_level_match:
                    skill_level_data = skill_level_match.group(1)
                    des_pattern = r'des\s*=\s*function\(\)\s*return\s*T\((.*?)\)\s*end'
                    des_match = re.search(des_pattern, skill_level_data, re.DOTALL)
                    if des_match:
                        des_content = des_match.group(1)
                        params = parse_t_function_params(des_content)
                        processed_params = process_t_function_params(params, word_dict)
                        all_params.append(processed_params)
    except Exception:
        logger.debug(f"提取多等级技能信息出错: skill_id={skill_id}")
        return f"{skill_name}\n未知"

    # 检查觉醒技能
    awakening_info = ""
    try:
        if skill_match:
            skill_data = skill_match.group(1)
            association_pattern = r'association_skills\s*=\s*\{\s*"([^"]+)"'
            association_match = re.search(association_pattern, skill_data, re.DOTALL)
            if association_match:
                association_content = association_match.group(1)
                parts = association_content.split(':')
                if len(parts) == 3:
                    association_skill_id = int(parts[1])
                    awakening_info = extract_awakening_info(association_skill_id, skill_level_up_file_path, word_dict)
    except Exception:
        logger.debug(f"提取觉醒技能信息出错: skill_id={skill_id}")

    if all_params:
        merged_params = merge_t_function_params(all_params)
        try:
            main_id = int(merged_params[0])
            main_text = word_dict.get(main_id, f"未知({main_id})")
            processed_params = []
            for param in merged_params[1:]:
                if isinstance(param, str) and param.startswith('T(') and param.endswith(')'):
                    nested_content = param[2:-1]
                    nested_parts = parse_t_function_params(nested_content)
                    nested_processed = process_t_function_params(nested_parts, word_dict)
                    if nested_processed:
                        try:
                            nested_id = int(nested_processed[0])
                            nested_text = word_dict.get(nested_id, f"未知({nested_id})")
                            for i, nested_param in enumerate(nested_processed[1:]):
                                if "%s" in nested_text:
                                    nested_text = nested_text.replace("%s", str(nested_param), 1)
                                elif "%d" in nested_text:
                                    nested_text = nested_text.replace("%d", str(nested_param), 1)
                            nested_text = nested_text.replace("%%", "%")
                            processed_params.append(nested_text)
                        except ValueError:
                            processed_params.append(f"参数错误: {nested_processed[0]}")
                else:
                    processed_params.append(param)
            for param in processed_params:
                if "%s" in main_text:
                    main_text = main_text.replace("%s", str(param), 1)
                elif "%d" in main_text:
                    main_text = main_text.replace("%d", str(param), 1)
            main_text = main_text.replace("%%", "%")
            main_text = re.sub(r'\[color=#[0-9a-fA-F]+\]', '', main_text)
            main_text = re.sub(r'\[/color\]', '', main_text)
            main_text = main_text.replace("\\n", "\n")
            return f"{skill_name}\n{main_text}{awakening_info}"
        except Exception:
            logger.debug(f"处理合并参数出错: skill_id={skill_id}")
    return f"{skill_name}\n未知{awakening_info}"


# ---------------------------------------------------------------------------
# 消耗计算
# ---------------------------------------------------------------------------

def get_breakthrough_cost(char_id, quality_up_dict, item_dict):
    """获取突破消耗"""
    breakthrough_costs = ["", "", "", ""]
    for i in range(4):
        quality_id = char_id * 1000 + i
        if quality_id in quality_up_dict:
            cost_items = quality_up_dict[quality_id].get("cost", [])
            cost_strings = []
            for item_id, item_count in cost_items:
                item_name = item_dict.get(item_id, f"未知物品({item_id})")
                cost_strings.append(f"{item_name} * {item_count}")
            if cost_strings:
                breakthrough_costs[i] = " | ".join(cost_strings)
    return breakthrough_costs


def get_normal_skill_upgrade_cost(normal_skill_id, skill_level_up_dict, item_dict):
    """获取普通技能升级消耗"""
    upgrade_costs = ["", "", ""]
    for i in range(2, 5):
        skill_level_id = normal_skill_id * 1000 + i
        if skill_level_id in skill_level_up_dict:
            cost_items = skill_level_up_dict[skill_level_id]
            cost_strings = []
            for item_id, item_count in cost_items:
                item_name = item_dict.get(item_id, f"未知物品({item_id})")
                cost_strings.append(f"{item_name} * {item_count}")
            if cost_strings:
                upgrade_costs[i - 2] = " | ".join(cost_strings)
    return upgrade_costs


def get_passive_skill_upgrade_cost(passive_skill_id, skill_level_up_dict, item_dict):
    """获取被动技能升级消耗"""
    upgrade_costs = ["", "", ""]
    for i in range(1, 4):
        skill_level_id = passive_skill_id * 1000 + i
        if skill_level_id in skill_level_up_dict:
            cost_items = skill_level_up_dict[skill_level_id]
            cost_strings = []
            for item_id, item_count in cost_items:
                item_name = item_dict.get(item_id, f"未知物品({item_id})")
                cost_strings.append(f"{item_name} * {item_count}")
            if cost_strings:
                upgrade_costs[i - 1] = " | ".join(cost_strings)
    return upgrade_costs


# ---------------------------------------------------------------------------
# BaseCard.lua 完整解析
# ---------------------------------------------------------------------------

def parse_basecard_file(file_path, word_dict, cv_dict, level_up_dict,
                        quality_up_dict, skill_file_path, skill_level_up_file_path,
                        badge_suit_dict):
    """完整解析 BaseCard.lua，返回角色数据字典（与参考脚本完全一致）"""
    TYPE_MAPPING = {1: "坚甲", 2: "异刃", 4: "言灵", 5: "猎影"}
    ELEMENT_MAPPING = ELEMENT_MAP
    # 语音类型映射（按顺序34条）
    VOICE_TYPE_MAPPING = [
        "成员报道", "问候", "闲谈1", "闲谈2", "闲谈3", "突破感悟1", "突破感悟2", "突破感悟3",
        "觉醒感悟1", "觉醒感悟2", "觉醒感悟3", "觉醒感悟4", "觉醒感悟5", "出战", "攻击1", "攻击2",
        "攻击3", "战技1", "战技2", "总攻技1", "总攻技2", "总攻技3", "受击1", "受击2", "受击3",
        "重伤", "退场", "作战胜利", "作战失败", "生日祝福", "新年祝福", "情人节祝福", "万圣节祝福", "圣诞节祝福"
    ]

    with open(file_path, 'r', encoding='utf-8') as f:
        content = f.read()

    characters = {}
    pattern = r'\[(\d+)\] = (\{(?:[^{}]|(?:\{(?:[^{}]|(?:\{[^{}]*\}))*\}))*\})'
    matches = re.findall(pattern, content, re.DOTALL)

    for match in matches:
        char_id, char_data = match
        char_id = int(char_id)

        # 提取普通技能ID
        normal_skill_id = 0
        normal_skill_pattern = r'normal_skill\s*=\s*(\d+)'
        normal_skill_match = re.search(normal_skill_pattern, char_data)
        if normal_skill_match:
            normal_skill_id = int(normal_skill_match.group(1))

        # 提取第一个被动技能ID
        first_passive_skill_id = 0
        grow_skills_pattern = r'grow_skill_ids\s*=\s*\{([^}]*)\}'
        grow_skills_match = re.search(grow_skills_pattern, char_data)
        if grow_skills_match:
            grow_skills_content = grow_skills_match.group(1)
            grow_skill_ids = re.findall(r'(\d+)', grow_skills_content)
            if len(grow_skill_ids) > 0:
                first_passive_skill_id = int(grow_skill_ids[0])

        # 提取名称
        name_pattern = r'name = function\(\)\s*return T\((\d+)\)\s*end'
        name_match = re.search(name_pattern, char_data)
        eng_name_pattern = r'name_english = function\(\)\s*return T\((\d+)\)\s*end'
        eng_name_match = re.search(eng_name_pattern, char_data)

        if not (name_match and eng_name_match):
            continue

        name_key = int(name_match.group(1))
        eng_name_key = int(eng_name_match.group(1))
        chinese_name = word_dict.get(name_key, f"未知({name_key})")
        english_name = word_dict.get(eng_name_key, f"Unknown({eng_name_key})")

        # 提取星级
        star = "未知"
        star_match = re.search(r'star\s*=\s*(\d+)', char_data)
        if star_match:
            star = star_match.group(1)

        # 提取初始属性
        init_hp = 0
        init_atk = 0
        init_def = 0
        hp_match = re.search(r'max_hp\s*=\s*(\d+)', char_data)
        atk_match = re.search(r'atk\s*=\s*(\d+)', char_data)
        def_match = re.search(r'def\s*=\s*(\d+)', char_data)
        if hp_match:
            init_hp = int(hp_match.group(1))
        if atk_match:
            init_atk = int(atk_match.group(1))
        if def_match:
            init_def = int(def_match.group(1))

        # 成长模型ID
        grow_model_id = 0
        grow_model_match = re.search(r'grow_model_id\s*=\s*(\d+)', char_data)
        if grow_model_match:
            grow_model_id = int(grow_model_match.group(1))

        quality_max = 0
        quality_max_match = re.search(r'quality_max\s*=\s*(\d+)', char_data)
        if quality_max_match:
            quality_max = int(quality_max_match.group(1))

        # 计算满级满破属性
        max_hp, max_atk, max_def = init_hp, init_atk, init_def
        if grow_model_id:
            level_up_id = grow_model_id * 1000 + 340
            level_up_attr = level_up_dict.get(level_up_id, {})
            max_hp += level_up_attr.get("生命", 0)
            max_atk += level_up_attr.get("攻击", 0)
            max_def += level_up_attr.get("防御", 0)
            quality_up_id = char_id * 1000 + quality_max
            quality_up_attr = quality_up_dict.get(quality_up_id, {})
            quality_up_attr = quality_up_attr.get('add_attr', {}) if isinstance(quality_up_attr, dict) else {}
            max_hp += quality_up_attr.get("生命", 0)
            max_atk += quality_up_attr.get("攻击", 0)
            max_def += quality_up_attr.get("防御", 0)

        # 提取职业
        profession = "未知"
        type_match = re.search(r'type\s*=\s*(\d+)', char_data)
        if type_match:
            type_id = int(type_match.group(1))
            profession = TYPE_MAPPING.get(type_id, f"未知({type_id})")

        # 提取属性
        element = "未知"
        element_match = re.search(r'element_type\s*=\s*\{(\d+)\}', char_data)
        if element_match:
            element_id = int(element_match.group(1))
            element = ELEMENT_MAPPING.get(element_id, f"未知({element_id})")

        # 提取生日
        birthday = "未知"
        info1_match = re.search(r'information1\s*=\s*function\(\)\s*return\s*T\(\d+,\s*(\d+),\s*(\d+)\)\s*end', char_data)
        if info1_match:
            month = info1_match.group(1)
            day = info1_match.group(2)
            birthday = f"{month}/{day}"

        # 提取身高
        height = "未知"
        info2_match = re.search(r'information2\s*=\s*function\(\)\s*return\s*T\(\d+,\s*(\d+)\)\s*end', char_data)
        if info2_match:
            height_value = info2_match.group(1)
            height = f"{height_value}cm"

        # 提取阵营
        faction = "未知"
        info3_match = re.search(r'information3\s*=\s*function\(\)\s*return\s*T\((\d+)\)\s*end', char_data)
        if info3_match:
            faction_key = int(info3_match.group(1))
            faction = word_dict.get(faction_key, f"未知({faction_key})")

        # 提取声优
        cv_info = "未知"
        cv_match = re.search(r'cv_name\s*=\s*(\d+)', char_data)
        if cv_match:
            cv_id = int(cv_match.group(1))
            cv_info = cv_dict.get(cv_id, f"未知({cv_id})")

        # 提取描述
        description = "未知"
        des_match = re.search(r'des\s*=\s*function\(\)\s*return\s*T\((\d+)\)\s*end', char_data)
        des1_match = re.search(r'des1\s*=\s*function\(\)\s*return\s*T\((\d+)\)\s*end', char_data)
        if des_match and des1_match:
            des_key = int(des_match.group(1))
            des1_key = int(des1_match.group(1))
            des_text = word_dict.get(des_key, f"未知({des_key})")
            des1_text = word_dict.get(des1_key, f"未知({des1_key})")
            description = f"{des_text}\n——{des1_text}"

        # 提取队长技能
        leader_skill_info = "未知"
        leader_skill_match = re.search(r'leader_skill\s*=\s*(\d+)', char_data)
        if leader_skill_match:
            leader_skill_id = int(leader_skill_match.group(1))
            leader_skill_info = extract_skill_info(leader_skill_id, skill_file_path, skill_level_up_file_path, word_dict)

        # 提取普通技能
        normal_skill_info = "未知"
        if normal_skill_match:
            normal_skill_id = int(normal_skill_match.group(1))
            normal_skill_info = extract_multi_level_skill_info_new(normal_skill_id, skill_file_path, skill_level_up_file_path, word_dict)

        # 提取特殊技能
        special_skill_info = "未知"
        special_skill_match = re.search(r'special_skill\s*=\s*(\d+)', char_data)
        if special_skill_match:
            special_skill_id = int(special_skill_match.group(1))
            special_skill_info = extract_multi_level_skill_info_new(special_skill_id, skill_file_path, skill_level_up_file_path, word_dict)

        # 提取爆发技能
        burst_skill_info = "未知"
        burst_skill_match = re.search(r'burst_skill\s*=\s*(\d+)', char_data)
        if burst_skill_match:
            burst_skill_id = int(burst_skill_match.group(1))
            burst_skill_info = extract_multi_level_skill_info_new(burst_skill_id, skill_file_path, skill_level_up_file_path, word_dict, is_burst_skill=True)

        # 提取被动技能
        passive_skill_1_info = "未知"
        passive_skill_2_info = "未知"
        passive_skill_3_info = "未知"
        if grow_skills_match:
            grow_skills_content = grow_skills_match.group(1)
            grow_skill_ids = re.findall(r'(\d+)', grow_skills_content)
            if len(grow_skill_ids) > 0:
                passive_skill_1_id = int(grow_skill_ids[0])
                passive_skill_1_info = extract_multi_level_skill_info_new(passive_skill_1_id, skill_file_path, skill_level_up_file_path, word_dict, 3)
            if len(grow_skill_ids) > 1:
                passive_skill_2_id = int(grow_skill_ids[1])
                passive_skill_2_info = extract_multi_level_skill_info_new(passive_skill_2_id, skill_file_path, skill_level_up_file_path, word_dict, 3)
            if len(grow_skill_ids) > 2:
                passive_skill_3_id = int(grow_skill_ids[2])
                passive_skill_3_info = extract_multi_level_skill_info_new(passive_skill_3_id, skill_file_path, skill_level_up_file_path, word_dict, 3)

        # 提取觉醒技能
        awakening_skill_1_info = "未知"
        awakening_skill_2_info = "未知"
        awakening_skill_3_info = "未知"
        awakening_skill_4_info = "未知"
        awakening_skill_5_info = "未知"
        unlock_skills_match = re.search(r'unlock_skill_ids\s*=\s*\{([^}]*)\}', char_data, re.DOTALL)
        if unlock_skills_match:
            unlock_skills_content = unlock_skills_match.group(1)
            unlock_skill_ids = re.findall(r'(\d+)', unlock_skills_content)
            for i, skill_id in enumerate(unlock_skill_ids):
                if i < 5:
                    skill_id_int = int(skill_id)
                    skill_info = extract_awakening_skill_info(skill_id_int, skill_file_path, skill_level_up_file_path, word_dict)
                    if i == 0:
                        awakening_skill_1_info = skill_info
                    elif i == 1:
                        awakening_skill_2_info = skill_info
                    elif i == 2:
                        awakening_skill_3_info = skill_info
                    elif i == 3:
                        awakening_skill_4_info = skill_info
                    elif i == 4:
                        awakening_skill_5_info = skill_info

        # 提取语音
        voice_lines = [''] * 34
        sound_match = re.search(r'sound_ids\s*=\s*\{([^}]*)\}', char_data, re.DOTALL)
        if sound_match:
            sound_content = sound_match.group(1)
            sound_ids = re.findall(r'(\d+)', sound_content)
            if len(sound_ids) == 34:
                for i, sound_id in enumerate(sound_ids):
                    if i < 34:
                        voice_text = word_dict.get(int(sound_id), "未知")
                        voice_lines[i] = f'"{voice_text}"'
            elif len(sound_ids) == 32:
                for i, sound_id in enumerate(sound_ids):
                    if i < 32:
                        if i < 2:
                            pos = i
                        elif i < 4:
                            pos = i
                        elif i < 7:
                            pos = i + 1
                        elif i < 12:
                            pos = i + 1
                        elif i < 13:
                            pos = i + 1
                        elif i < 16:
                            pos = i + 1
                        elif i < 18:
                            pos = i + 1
                        elif i < 20:
                            pos = i + 1
                        elif i < 23:
                            pos = i + 2
                        else:
                            pos = i + 2
                        voice_text = word_dict.get(int(sound_id), "未知")
                        voice_lines[pos] = f'"{voice_text}"'
            else:
                for i, sound_id in enumerate(sound_ids):
                    if i < 34:
                        voice_text = word_dict.get(int(sound_id), "未知")
                        voice_lines[i] = f'"{voice_text}"'

        # 提取角色故事
        personal_info = "未知"
        anecdote = "未知"
        record = "未知"
        anecdote2 = "未知"
        story_match = re.search(r'story_ids\s*=\s*\{([^}]*)\}', char_data, re.DOTALL)
        if story_match:
            story_content = story_match.group(1)
            story_items = re.findall(r'"([^"]+)"', story_content)
            for i, story_item in enumerate(story_items):
                if ':' in story_item:
                    story_id_str, story_type = story_item.split(':')
                    story_id = int(story_id_str)
                    story_text = word_dict.get(story_id, f"未知({story_id})")
                    story_text = re.sub(r'<[^>]+>', '', story_text)
                    story_text = re.sub(r'&[a-z]+;', '', story_text)
                    story_text = re.sub(r'<style[^>]*>.*?</style>', '', story_text, flags=re.DOTALL)
                    story_text = re.sub(r'<script[^>]*>.*?</script>', '', story_text, flags=re.DOTALL)
                    story_text = re.sub(r'<!--.*?-->', '', story_text, flags=re.DOTALL)
                    story_text = re.sub(r'<p style=\'text-align: right;\'>.*?</p>', '', story_text)
                    story_text = re.sub(r'<span.*?>.*?</span>', '', story_text)
                    story_text = re.sub(r'<.*?>', '', story_text)
                    story_text = re.sub(r'\s+', ' ', story_text)
                    story_text = story_text.strip()
                    story_text = story_text.replace("\\n", "\n")
                    if i == 0:
                        personal_info = story_text
                    elif i == 1:
                        anecdote = story_text
                    elif i == 2:
                        record = story_text
                    elif i == 3:
                        anecdote2 = story_text

        # 提取徽章信息
        badge_info = ""
        badge_suit_match = re.search(r'badge_suit_ids\s*=\s*\{([^}]*)\}', char_data, re.DOTALL)
        badge_main_match = re.search(r'badge_main_attribute\s*=\s*\{([^}]*)\}', char_data, re.DOTALL)
        badge_vice_match = re.search(r'badge_vice_attribute\s*=\s*\{([^}]*)\}', char_data, re.DOTALL)
        if badge_suit_match or badge_main_match or badge_vice_match:
            badge_suit_names = []
            if badge_suit_match:
                badge_suit_content = badge_suit_match.group(1)
                badge_suit_ids = re.findall(r'(\d+)', badge_suit_content)
                for suit_id in badge_suit_ids:
                    suit_name = badge_suit_dict.get(int(suit_id), f"未知({suit_id})")
                    badge_suit_names.append(suit_name)
            main_attrs = []
            if badge_main_match:
                badge_main_content = badge_main_match.group(1)
                badge_main_items = re.findall(r'"([^"]+)"', badge_main_content)
                for main_item in badge_main_items:
                    if ':' in main_item:
                        attr_ids = main_item.split(':')
                        attr_names = []
                        for attr_id in attr_ids:
                            text_id = int('8' + attr_id[1:])
                            attr_name = word_dict.get(text_id, f"未知({text_id})")
                            attr_names.append(attr_name)
                        main_attrs.append(' '.join(attr_names))
                    else:
                        text_id = int('8' + main_item[1:])
                        attr_name = word_dict.get(text_id, f"未知({text_id})")
                        main_attrs.append(attr_name)
            vice_attrs = []
            if badge_vice_match:
                badge_vice_content = badge_vice_match.group(1)
                badge_vice_ids = re.findall(r'(\d+)', badge_vice_content)
                for vice_id in badge_vice_ids:
                    text_id = int('8' + vice_id[1:])
                    attr_name = word_dict.get(text_id, f"未知({text_id})")
                    vice_attrs.append(attr_name)
            badge_info_parts = []
            if badge_suit_names:
                badge_info_parts.append("推荐徽章")
                badge_info_parts.append("/".join(badge_suit_names))
                badge_info_parts.append("")
            if main_attrs:
                badge_info_parts.append("推荐主属性")
                badge_info_parts.append("//".join(main_attrs))
                badge_info_parts.append("")
            if vice_attrs:
                badge_info_parts.append("推荐副属性")
                badge_info_parts.append(" ".join(vice_attrs))
            badge_info = "\n".join(badge_info_parts)

        # 提取其他属性
        crt_value = "未知"
        crt_match = re.search(r'crt\s*=\s*(\d+)', char_data)
        if crt_match:
            crt_value = crt_match.group(1)
        blk_value = "未知"
        blk_match = re.search(r'blk\s*=\s*(\d+)', char_data)
        if blk_match:
            blk_value = blk_match.group(1)
        crt_int_value = "未知"
        crt_int_match = re.search(r'crt_int\s*=\s*(\d+)', char_data)
        if crt_int_match:
            crt_int_value = crt_int_match.group(1)
        blk_int_value = "未知"
        blk_int_match = re.search(r'blk_int\s*=\s*(\d+)', char_data)
        if blk_int_match:
            blk_int_value = blk_int_match.group(1)
        spd_move_value = "未知"
        spd_move_match = re.search(r'spd_move\s*=\s*(\d+)', char_data)
        if spd_move_match:
            spd_move_value = spd_move_match.group(1)
        spd_atk_value = "未知"
        spd_atk_match = re.search(r'spd_atk\s*=\s*(\d+)', char_data)
        if spd_atk_match:
            spd_atk_value = spd_atk_match.group(1)
        range_atk_value = "未知"
        range_atk_match = re.search(r'range_atk\s*=\s*(\d+)', char_data)
        if range_atk_match:
            range_atk_value = range_atk_match.group(1)
        weight_value = "未知"
        weight_match = re.search(r'weight\s*=\s*(\d+)', char_data)
        if weight_match:
            weight_value = weight_match.group(1)

        characters[char_id] = {
            "raw_id": name_key,
            "name": f"{chinese_name}/{english_name}",
            "star": star,
            "profession": profession,
            "element": element,
            "birthday": birthday,
            "height": height,
            "faction": faction,
            "cv": cv_info,
            "description": description,
            "init_atk": init_atk, "init_def": init_def, "init_hp": init_hp,
            "max_atk": max_atk, "max_def": max_def, "max_hp": max_hp,
            "crt": crt_value, "blk": blk_value,
            "crt_int": crt_int_value, "blk_int": blk_int_value,
            "spd_move": spd_move_value, "spd_atk": spd_atk_value, "range_atk": range_atk_value,
            "weight": weight_value,
            "leader_skill": leader_skill_info,
            "normal_skill": normal_skill_info,
            "special_skill": special_skill_info,
            "burst_skill": burst_skill_info,
            "passive_skill_1": passive_skill_1_info,
            "passive_skill_2": passive_skill_2_info,
            "passive_skill_3": passive_skill_3_info,
            "awakening_skill_1": awakening_skill_1_info,
            "awakening_skill_2": awakening_skill_2_info,
            "awakening_skill_3": awakening_skill_3_info,
            "awakening_skill_4": awakening_skill_4_info,
            "awakening_skill_5": awakening_skill_5_info,
            "voice_1": voice_lines[0], "voice_2": voice_lines[1], "voice_3": voice_lines[2],
            "voice_4": voice_lines[3], "voice_5": voice_lines[4], "voice_6": voice_lines[5],
            "voice_7": voice_lines[6], "voice_8": voice_lines[7], "voice_9": voice_lines[8],
            "voice_10": voice_lines[9], "voice_11": voice_lines[10], "voice_12": voice_lines[11],
            "voice_13": voice_lines[12], "voice_14": voice_lines[13], "voice_15": voice_lines[14],
            "voice_16": voice_lines[15], "voice_17": voice_lines[16], "voice_18": voice_lines[17],
            "voice_19": voice_lines[18], "voice_20": voice_lines[19], "voice_21": voice_lines[20],
            "voice_22": voice_lines[21], "voice_23": voice_lines[22], "voice_24": voice_lines[23],
            "voice_25": voice_lines[24], "voice_26": voice_lines[25], "voice_27": voice_lines[26],
            "voice_28": voice_lines[27], "voice_29": voice_lines[28], "voice_30": voice_lines[29],
            "voice_31": voice_lines[30], "voice_32": voice_lines[31], "voice_33": voice_lines[32],
            "voice_34": voice_lines[33],
            "personal_info": personal_info,
            "anecdote": anecdote,
            "record": record,
            "anecdote2": anecdote2,
            "badge_info": badge_info,
            "normal_skill_id": normal_skill_id,
            "first_passive_skill_id": first_passive_skill_id
        }

    logger.debug(f"BaseCard.lua 解析完成: {len(characters)} 个角色")
    return characters


# ---------------------------------------------------------------------------
# 角色数据整体加载（纯解析，供 UI 协调层调用）
# ---------------------------------------------------------------------------

# 角色 ID 过滤范围
CHARACTER_ID_MIN = 80100001
CHARACTER_ID_MAX = 80101999


def load_character_data(lua_dir, progress_callback=None):
    """纯解析逻辑：从解密后的 Lua 文件加载角色数据（无 Qt 依赖，可独立测试）。

    参数:
        lua_dir: 解密后的 Lua 文件所在目录
        progress_callback: 可选回调，签名 progress_callback(progress, message)

    返回:
        (characters, characters_full, word_map)
        - characters: 过滤后用于表格显示的角色列表，元素为
          {name, char_id, raw_id, display_index}
        - characters_full: BaseCard 完整解析结果（char_id -> 数据字典）
        - word_map: BaseWord 文本映射（文本 id -> 文本）
    """
    def report(prog, msg):
        if progress_callback:
            progress_callback(prog, msg)

    # 0. 目录检查
    if not os.path.isdir(lua_dir):
        logger.warning(f"Lua 目录不存在: {lua_dir}")
        return [], {}, {}

    # 1. 读取 BaseWord_cn.lua → word_dict
    bw_path = os.path.join(lua_dir, "BaseWord_cn.lua")
    if not os.path.isfile(bw_path):
        logger.warning("BaseWord_cn.lua 不存在, 角色数据无法加载")
        return [], {}, {}
    report(10, "正在解析 BaseWord_cn.lua...")
    word_dict = parse_word_file(bw_path)
    logger.info(f"BaseWord 文本映射提取: {len(word_dict)} 条")

    # 2. 读取 BaseCvNameCn.lua → cv_dict
    report(20, "正在解析 BaseCvNameCn.lua...")
    cv_path = os.path.join(lua_dir, "BaseCvNameCn.lua")
    cv_dict = {}
    if os.path.isfile(cv_path):
        cv_dict = parse_cv_file(cv_path)
    logger.info(f"BaseCvNameCn 解析完成: {len(cv_dict)} 条")

    # 3. 读取 BaseCardLevelUp.lua → level_up_dict
    report(30, "正在解析 BaseCardLevelUp.lua...")
    lv_path = os.path.join(lua_dir, "BaseCardLevelUp.lua")
    level_up_dict = {}
    if os.path.isfile(lv_path):
        level_up_dict = parse_level_up_file(lv_path)
    logger.info(f"BaseCardLevelUp 解析完成: {len(level_up_dict)} 条")

    # 4. 读取 BaseCardQualityUp.lua → quality_up_dict（带消耗）
    report(40, "正在解析 BaseCardQualityUp.lua...")
    qu_path = os.path.join(lua_dir, "BaseCardQualityUp.lua")
    quality_up_dict = {}
    if os.path.isfile(qu_path):
        quality_up_dict = parse_quality_up_file_with_cost(qu_path)
    logger.info(f"BaseCardQualityUp 解析完成: {len(quality_up_dict)} 条")

    # 5. 读取 BaseSkill.lua + BaseSkillLevelUp.lua
    report(50, "正在解析 BaseSkill + BaseSkillLevelUp...")
    sk_path = os.path.join(lua_dir, "BaseSkill.lua")
    slu_path = os.path.join(lua_dir, "BaseSkillLevelUp.lua")
    skill_name_map = {}
    skill_desc_map = {}
    skill_to_upgrade = {}
    if os.path.isfile(sk_path) and os.path.isfile(slu_path):
        with open(sk_path, 'r', encoding='utf-8') as f:
            sk_content = f.read()
        for m in re.finditer(r'\[\s*(\d+)\s*\]\s*=\s*\{[^}]*?name\s*=\s*function\(\)\s*return\s*T\((\d+)\)\s*end', sk_content):
            sid = int(m.group(1))
            tid = int(m.group(2))
            skill_name_map[sid] = word_dict.get(tid, str(sid))

        with open(slu_path, 'r', encoding='utf-8') as f:
            slu_content = f.read()
        lu_pat = re.compile(r'\[\s*(\d+)\s*\]\s*=\s*\{[^}]*?des\s*=\s*function\(\)\s*return\s*T\(')
        for m in lu_pat.finditer(slu_content):
            upgrade_id = int(m.group(1))
            start = m.end()
            depth = 1
            pos = start
            while pos < len(slu_content) and depth > 0:
                if slu_content[pos] == '(':
                    depth += 1
                elif slu_content[pos] == ')':
                    depth -= 1
                pos += 1
            if depth != 0:
                continue
            des_args_str = slu_content[start:pos - 1].strip()
            params = parse_t_function_params(des_args_str)
            processed_params = process_t_function_params(params, word_dict)
            if processed_params:
                try:
                    main_id = int(processed_params[0])
                    main_text = word_dict.get(main_id, f"未知({main_id})")
                    for i, param in enumerate(processed_params[1:]):
                        if "%s" in main_text:
                            main_text = main_text.replace("%s", str(param), 1)
                        elif "%d" in main_text:
                            main_text = main_text.replace("%d", str(param), 1)
                    main_text = main_text.replace("%%", "%")
                    main_text = main_text.replace("\\n", "\n")
                    main_text = re.sub(r'\[color=#[0-9a-fA-F]+\]', '', main_text)
                    main_text = re.sub(r'\[/color\]', '', main_text)
                    skill_desc_map[upgrade_id] = main_text
                except (ValueError, IndexError):
                    logger.debug(f"技能描述解析失败: upgrade_id={upgrade_id}")

        # 建立 skill_id -> first_upgrade_id 映射
        for uid in sorted(skill_desc_map.keys()):
            skill_part = uid // 1000
            if skill_part not in skill_to_upgrade:
                skill_to_upgrade[skill_part] = uid

    logger.info(f"BaseSkill 解析: {len(skill_name_map)} 个技能名称, {len(skill_desc_map)} 条描述")

    # 6. 读取 BaseBadgeSuitGroup.lua → badge_suit_dict
    report(60, "正在解析 BaseBadgeSuitGroup.lua...")
    bg_path = os.path.join(lua_dir, "BaseBadgeSuitGroup.lua")
    badge_suit_dict = {}
    if os.path.isfile(bg_path):
        badge_suit_dict = parse_badge_suit_file(bg_path, word_dict)
    logger.info(f"BaseBadgeSuitGroup 解析完成: {len(badge_suit_dict)} 套")

    # 7. 读取 BaseItem.lua → item_dict
    report(70, "正在解析 BaseItem.lua...")
    it_path = os.path.join(lua_dir, "BaseItem.lua")
    item_dict = {}
    if os.path.isfile(it_path):
        item_dict = parse_item_file(it_path, word_dict)
    logger.info(f"BaseItem 解析完成: {len(item_dict)} 个物品")

    # 8. 读取 BaseSkillLevelUp.lua → skill_level_up_dict（带消耗）
    report(80, "正在解析技能升级消耗...")
    skill_level_up_dict = {}
    if os.path.isfile(slu_path):
        skill_level_up_dict = parse_skill_level_up_file_with_cost(slu_path)
    logger.info(f"BaseSkillLevelUp 消耗解析完成: {len(skill_level_up_dict)} 条")

    # 9. 使用 parse_basecard_file 解析 BaseCard.lua 得到完整角色数据
    report(90, "正在解析 BaseCard.lua...")
    bc_path = os.path.join(lua_dir, "BaseCard.lua")
    if not os.path.isfile(bc_path):
        logger.warning("BaseCard.lua 不存在")
        return [], {}, word_dict
    characters_full = parse_basecard_file(
        bc_path, word_dict, cv_dict, level_up_dict,
        quality_up_dict, sk_path, slu_path, badge_suit_dict
    )

    # 10. 添加突破消耗和技能升级消耗
    for char_id, char_data in characters_full.items():
        char_data['breakthrough_costs'] = get_breakthrough_cost(char_id, quality_up_dict, item_dict)

        normal_skill_id = char_data.get('normal_skill_id', 0)
        if normal_skill_id:
            char_data['normal_skill_upgrade_costs'] = get_normal_skill_upgrade_cost(normal_skill_id, skill_level_up_dict, item_dict)
        else:
            char_data['normal_skill_upgrade_costs'] = ["", "", ""]

        first_passive_skill_id = char_data.get('first_passive_skill_id', 0)
        if first_passive_skill_id:
            char_data['passive_skill_upgrade_costs'] = get_passive_skill_upgrade_cost(first_passive_skill_id, skill_level_up_dict, item_dict)
        else:
            char_data['passive_skill_upgrade_costs'] = ["", "", ""]

    # 过滤 ID 范围 80100001~80101999
    filtered = {}
    for char_id, char_info in characters_full.items():
        raw_id = char_info.get("raw_id", 0)
        if CHARACTER_ID_MIN <= raw_id <= CHARACTER_ID_MAX:
            filtered[char_id] = char_info

    # 填充 characters 列表（用于表格显示）
    characters = []
    for char_id, char_info in sorted(filtered.items(), key=lambda x: x[1].get("raw_id", 0)):
        characters.append({
            "name": char_info.get("name", "未知").split('/')[0],
            "char_id": char_id,
            "raw_id": char_info.get("raw_id", 0),
            "display_index": char_info.get("raw_id", 0)
        })

    return characters, characters_full, word_dict