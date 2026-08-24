# -*- coding: utf-8 -*-
"""角色 Lua 解析的通用文本与 T() 参数工具。"""

import re

from app.platform.diagnostics import logger


def extract_all_card_blocks(card_content):
    """解析 BaseCard.lua 中所有 `[数字] = { ... }` 块。"""
    blocks = []
    pattern = re.compile(r'\[\s*(\d+)\s*\]\s*=\s*\{')
    for match in pattern.finditer(card_content):
        raw_id = int(match.group(1))
        start = match.end() - 1
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
            blocks.append((raw_id, card_content[match.start():pos]))
    return blocks


def extract_t_references(block_text):
    """从块文本中提取所有 T(数字) 引用的 raw_id 集合。"""
    return set(int(value) for value in re.findall(r'T\((\d+)\)', block_text))


def parse_skill_up_args(args_str):
    """解析 BaseSkillLevelUp.lua 中 des 的 T() 参数列表。"""
    result = []

    def replace_t(match):
        parts = [int(value) for value in re.findall(r'\d+', match.group(1))]
        return str(parts[1]) if len(parts) >= 2 else "0"

    processed = re.sub(r'T\(([^)]*)\)', replace_t, args_str)
    for num_str in re.findall(r'\d+', processed):
        result.append(int(num_str))
    return result


def parse_t_args(value):
    """解析 T() 内部的参数列表，处理嵌套 T() 中的逗号。"""
    args = []
    depth = 0
    current = []
    for char in value:
        if char == '(':
            depth += 1
            current.append(char)
        elif char == ')':
            depth -= 1
            current.append(char)
        elif char == ',' and depth == 0:
            args.append(''.join(current).strip())
            current = []
        else:
            current.append(char)
    if current:
        args.append(''.join(current).strip())
    return args


def resolve_t_call(call_str, word_map):
    """递归解析 T(id, arg1, arg2, ...) 格式的调用。"""
    call_str = call_str.strip()
    try:
        int(call_str)
        return call_str
    except ValueError:
        pass
    args = parse_t_args(call_str)
    if not args:
        return call_str
    try:
        template_id = int(args[0].strip())
    except ValueError:
        return call_str
    template = word_map.get(template_id, "")
    if not template:
        logger.debug(f"resolve_t_call: 模板缺失 template_id={template_id}")
        return str(template_id)
    values = []
    for arg in args[1:]:
        arg = arg.strip()
        if arg.startswith('T(') and arg.endswith(')'):
            values.append(resolve_t_call(arg[2:-1], word_map))
        else:
            values.append(arg)
    typed_values = []
    for value in values:
        try:
            typed_values.append(float(value) if '.' in value else int(value))
        except (ValueError, TypeError):
            typed_values.append(value)
    try:
        result = template % tuple(typed_values)
    except (TypeError, ValueError, IndexError):
        result = template
        for value in values:
            result = result.replace('%s', str(value), 1)
            result = result.replace('%d', str(value), 1)
    return result


def parse_t_function_params(content):
    """解析 T 函数参数。"""
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
    """处理 T 函数参数（包含嵌套 T）。"""
    processed_params = []
    for param in params:
        if isinstance(param, str) and param.startswith('T(') and param.endswith(')'):
            nested_parts = parse_t_function_params(param[2:-1])
            nested_processed = process_t_function_params(nested_parts, word_dict)
            if nested_processed:
                try:
                    nested_id = int(nested_processed[0])
                    nested_text = word_dict.get(nested_id, f"未知({nested_id})")
                    for nested_param in nested_processed[1:]:
                        if "%s" in nested_text:
                            nested_text = nested_text.replace("%s", str(nested_param), 1)
                        elif "%d" in nested_text:
                            nested_text = nested_text.replace("%d", str(nested_param), 1)
                    processed_params.append(nested_text.replace("%%", "%"))
                except ValueError:
                    processed_params.append(f"参数错误: {nested_processed[0]}")
            else:
                processed_params.append(param)
        else:
            processed_params.append(param)
    return processed_params


def merge_t_function_params(all_params):
    """合并多级 T 函数参数。"""
    if not all_params:
        return []
    if not all(len(params) == len(all_params[0]) for params in all_params):
        return all_params[0]
    merged_params = []
    for index in range(len(all_params[0])):
        values = [params[index] for params in all_params]
        if all('%' in str(value) for value in values):
            numeric_parts = [str(value).split('%', 1)[0] for value in values]
            if all(value == numeric_parts[0] for value in numeric_parts):
                merged_params.append(f"{numeric_parts[0]}%")
            else:
                merged_params.append("/".join(numeric_parts) + "%")
        else:
            all_numeric = all(re.match(r'^-?\d+(\.\d+)?$', str(value)) for value in values)
            if all_numeric and all(value == values[0] for value in values):
                merged_params.append(values[0])
            elif all(value == values[0] for value in values):
                merged_params.append(values[0])
            else:
                merged_params.append("/".join(map(str, values)))
    return merged_params
