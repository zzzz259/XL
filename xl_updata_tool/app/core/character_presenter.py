"""角色详情展示与 CSV 导出逻辑，不依赖 Qt。"""

import csv
from collections.abc import Mapping


VOICE_LABELS = [
    "成员报道", "问候", "闲谈1", "闲谈2", "闲谈3",
    "突破感悟1", "突破感悟2", "突破感悟3",
    "觉醒感悟1", "觉醒感悟2", "觉醒感悟3", "觉醒感悟4", "觉醒感悟5",
    "出战", "攻击1", "攻击2", "攻击3", "战技1", "战技2",
    "总攻技1", "总攻技2", "总攻技3", "受击1", "受击2", "受击3",
    "重伤", "退场", "作战胜利", "作战失败",
    "生日祝福", "新年祝福", "情人节祝福", "万圣节祝福", "圣诞节祝福",
]

CSV_HEADERS = [
    "ID", "Name", "Star", "Profession", "Element", "Birthday", "Height", "Faction", "CV", "Description",
    "Init_ATK", "Init_DEF", "Init_HP", "Max_ATK", "Max_DEF", "Max_HP",
    "CRT", "BLK", "CRT_INT", "BLK_INT", "SPD_MOVE", "SPD_ATK", "RANGE_ATK", "WEIGHT",
    "Leader_Skill", "Normal_Skill", "Special_Skill", "Burst_Skill",
    "Passive_Skill_1", "Passive_Skill_2", "Passive_Skill_3",
    "觉醒1", "觉醒2", "觉醒3", "觉醒4", "觉醒5",
    "成员报道", "问候", "闲谈1", "闲谈2", "闲谈3",
    "突破感悟1", "突破感悟2", "突破感悟3",
    "觉醒感悟1", "觉醒感悟2", "觉醒感悟3", "觉醒感悟4", "觉醒感悟5",
    "出战", "攻击1", "攻击2", "攻击3", "战技1", "战技2",
    "总攻技1", "总攻技2", "总攻技3", "受击1", "受击2", "受击3",
    "重伤", "退场", "作战胜利", "作战失败",
    "生日祝福", "新年祝福", "情人节祝福", "万圣节祝福", "圣诞节祝福",
    "个人情报", "风闻", "记录", "逸事",
    "推荐徽章",
    "突破一", "突破二", "突破三", "突破四",
    "普通技能升级一", "普通技能升级二", "普通技能升级三",
    "被动技能升级一", "被动技能升级二", "被动技能升级三",
]


def _newline_to_html(value):
    return value.replace("\n", "<br/>") if value else ""


def _percent(value):
    if value and value != "未知":
        try:
            return f"{int(value) // 100}%"
        except (ValueError, TypeError):
            return str(value)
    return "-"


def build_character_detail_html(char: Mapping) -> tuple[str, str]:
    """返回角色标题和详情 HTML。"""
    name = char.get("name", "未知")
    name = name.split("/")[0] if "/" in str(name) else name

    html_parts = [
        "<h2>基础信息</h2>",
        f"<p><b>名称：</b>{name}</p>",
        f"<p><b>星级：</b>{char.get('star', '未知')}</p>",
        f"<p><b>职业：</b>{char.get('profession', '未知')}</p>",
        f"<p><b>属性：</b>{char.get('element', '未知')}</p>",
        f"<p><b>生日：</b>{char.get('birthday', '未知')}</p>",
        f"<p><b>身高：</b>{char.get('height', '未知')}</p>",
        f"<p><b>阵营：</b>{char.get('faction', '未知')}</p>",
        f"<p><b>CV：</b>{char.get('cv', '未知')}</p>",
        f"<p><b>简介：</b><br/>{_newline_to_html(char.get('description', '未知'))}</p>",
        "<h2>战斗属性</h2>",
        f"<p><b>初始生命：</b>{char.get('init_hp', 0)} → <b>满级生命：</b>{char.get('max_hp', 0)}</p>",
        f"<p><b>初始攻击：</b>{char.get('init_atk', 0)} → <b>满级攻击：</b>{char.get('max_atk', 0)}</p>",
        f"<p><b>初始防御：</b>{char.get('init_def', 0)} → <b>满级防御：</b>{char.get('max_def', 0)}</p>",
        f"<p><b>暴击：</b>{_percent(char.get('crt', '0'))} &nbsp; <b>格挡：</b>{_percent(char.get('blk', '0'))}</p>",
        f"<p><b>暴击伤害：</b>{_percent(char.get('crt_int', '0'))} &nbsp; <b>格挡效果：</b>{_percent(char.get('blk_int', '0'))}</p>",
        f"<p><b>移动速度：</b>{char.get('spd_move', '未知')} &nbsp; <b>攻击速度：</b>{char.get('spd_atk', '未知')} &nbsp; <b>攻击范围：</b>{char.get('range_atk', '未知')}</p>",
    ]

    html_parts.append("<h2>技能</h2>")
    for label, field in [
        ("队长技能", "leader_skill"),
        ("普通技能", "normal_skill"),
        ("特殊技能", "special_skill"),
        ("爆发技能", "burst_skill"),
    ]:
        value = char.get(field, "未知")
        if value and value != "未知":
            html_parts.append(f"<p><b>{label}：</b><br/>{_newline_to_html(value)}</p>")

    for prefix, label in [("passive_skill", "被动技能"), ("awakening_skill", "觉醒技能")]:
        limit = 4 if prefix == "passive_skill" else 6
        for index in range(1, limit):
            value = char.get(f"{prefix}_{index}", "未知")
            if value and value != "未知":
                html_parts.append(f"<p><b>{label}{index}：</b><br/>{_newline_to_html(value)}</p>")

    badge_info = char.get("badge_info", "")
    if badge_info:
        html_parts.extend(["<h2>徽章推荐</h2>", f"<p>{_newline_to_html(badge_info)}</p>"])

    for field, title, labels in [
        ("breakthrough_costs", "突破消耗", ["突破一", "突破二", "突破三", "突破四"]),
        ("normal_skill_upgrade_costs", "普通技能升级消耗", ["升级1", "升级2", "升级3"]),
        ("passive_skill_upgrade_costs", "被动技能升级消耗", ["升级1", "升级2", "升级3"]),
    ]:
        costs = char.get(field, [""] * len(labels))
        if any(costs):
            html_parts.append(f"<h2>{title}</h2>")
            for index, cost in enumerate(costs):
                if cost:
                    html_parts.append(f"<p><b>{labels[index]}：</b>{cost}</p>")

    html_parts.append("<h2>语音</h2>")
    for index, label in enumerate(VOICE_LABELS, start=1):
        value = char.get(f"voice_{index}", "")
        if value:
            html_parts.append(f"<p><b>{label}：</b>{value}</p>")

    html_parts.append("<h2>故事</h2>")
    for label, field in [("个人情报", "personal_info"), ("风闻", "anecdote"), ("记录", "record"), ("逸事", "anecdote2")]:
        value = char.get(field, "未知")
        if value and value != "未知":
            html_parts.append(f"<p><b>{label}：</b><br/>{_newline_to_html(value)}</p>")

    return str(name), "<html><body>" + "<br/>".join(html_parts) + "</body></html>"


def _csv_value(char_info: Mapping, key: str, default=""):
    value = char_info.get(key, default)
    return default if value is None else str(value)


def _csv_skill(char_info: Mapping, field: str, default=""):
    value = char_info.get(field, default)
    if value and value != "未知":
        return value.replace("\n", " | ").replace('"', "'")
    return default


def _csv_voice(char_info: Mapping, index: int):
    value = char_info.get(f"voice_{index}", "")
    return value.strip('"') if value else ""


def export_characters_csv(file_path: str, characters_full: Mapping) -> int:
    """导出角色数据 CSV，返回写入的角色数量。"""
    filtered = {
        char_id: info for char_id, info in characters_full.items()
        if 80100001 <= info.get("raw_id", 0) <= 80101999
    }
    if not filtered:
        return 0

    with open(file_path, "w", newline="", encoding="utf-8-sig") as handle:
        writer = csv.writer(handle)
        writer.writerow(CSV_HEADERS)
        for _char_id, char_info in sorted(filtered.items(), key=lambda item: item[1].get("raw_id", 0)):
            name = _csv_value(char_info, "name")
            row = [
                _csv_value(char_info, "raw_id"), name.split("/")[0] if "/" in name else name,
                _csv_value(char_info, "star"), _csv_value(char_info, "profession"),
                _csv_value(char_info, "element"), _csv_value(char_info, "birthday"),
                _csv_value(char_info, "height"), _csv_value(char_info, "faction"),
                _csv_value(char_info, "cv"), _csv_value(char_info, "description").replace("\n", " "),
                _csv_value(char_info, "init_atk"), _csv_value(char_info, "init_def"), _csv_value(char_info, "init_hp"),
                _csv_value(char_info, "max_atk"), _csv_value(char_info, "max_def"), _csv_value(char_info, "max_hp"),
                _csv_value(char_info, "crt"), _csv_value(char_info, "blk"), _csv_value(char_info, "crt_int"),
                _csv_value(char_info, "blk_int"), _csv_value(char_info, "spd_move"), _csv_value(char_info, "spd_atk"),
                _csv_value(char_info, "range_atk"), _csv_value(char_info, "weight"),
            ]
            row.extend(_csv_skill(char_info, field) for field in [
                "leader_skill", "normal_skill", "special_skill", "burst_skill",
                "passive_skill_1", "passive_skill_2", "passive_skill_3",
                "awakening_skill_1", "awakening_skill_2", "awakening_skill_3",
                "awakening_skill_4", "awakening_skill_5",
            ])
            row.extend(_csv_voice(char_info, index) for index in range(1, 35))
            row.extend(_csv_value(char_info, field).replace("\n", " ") for field in [
                "personal_info", "anecdote", "record", "anecdote2",
            ])
            row.append(_csv_value(char_info, "badge_info").replace("\n", " | "))
            for field, length in [
                ("breakthrough_costs", 4),
                ("normal_skill_upgrade_costs", 3),
                ("passive_skill_upgrade_costs", 3),
            ]:
                values = char_info.get(field, [""] * length)
                row.extend(values[index] if index < len(values) else "" for index in range(length))
            writer.writerow(row)
    return len(filtered)
