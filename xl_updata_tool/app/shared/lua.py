"""跨功能复用的基础 Lua 文本表解析工具。

这里只放不带角色或音频语义的格式解析；具体业务解析仍归各自 Feature。
"""

import re


def parse_word_file(file_path):
    """解析 ``BaseWord_cn.lua``，返回 ``{文本 ID: 文本}``。"""
    result = {}
    content = (
        file_path.read_text(encoding="utf-8")
        if hasattr(file_path, "read_text")
        else open(file_path, encoding="utf-8").read()
    )
    pattern = r'\[(\d+)\] = \{(.*?)\}(?=,|\n\s*\]|\n\s*\})'
    for key_id, item_content in re.findall(pattern, content, re.DOTALL):
        name_match = re.search(r'(?:name|sub_name) = "([^"]+)"', item_content)
        if name_match:
            result[int(key_id)] = name_match.group(1)
    for match in re.finditer(r'\[\s*(\d+)\s*\]\s*=\s*"([^"]*)"', content):
        result[int(match.group(1))] = match.group(2)
    return result
