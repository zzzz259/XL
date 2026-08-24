"""角色 Wiki 资料模型：将解析结果整理为可由不同 UI 展示的结构。"""

from dataclasses import dataclass
from collections.abc import Mapping

from .presenter import VOICE_LABELS


@dataclass(frozen=True)
class ProfileField:
    label: str
    value: str


@dataclass(frozen=True)
class ProfileStat:
    label: str
    initial: str
    maximum: str


@dataclass(frozen=True)
class ProfileSkill:
    label: str
    description: str


@dataclass(frozen=True)
class CharacterProfile:
    name: str
    raw_id: str
    summary: str
    identity: tuple[ProfileField, ...]
    primary_stats: tuple[ProfileStat, ...]
    secondary_stats: tuple[ProfileField, ...]
    skills: tuple[ProfileSkill, ...]
    progression: tuple[ProfileField, ...]
    story: tuple[ProfileField, ...]
    voices: tuple[ProfileField, ...]
    badge_info: str


def _value(char: Mapping, key: str, default="未知") -> str:
    value = char.get(key, default)
    if value is None or value == "":
        return str(default)
    return str(value)


def _optional_value(char: Mapping, key: str) -> str:
    value = char.get(key, "")
    return "" if value is None else str(value)


def _percent(value) -> str:
    if value in (None, "", "未知"):
        return "-"
    try:
        return f"{int(value) // 100}%"
    except (TypeError, ValueError):
        return str(value)


def _non_empty_fields(char: Mapping, pairs: list[tuple[str, str]]) -> tuple[ProfileField, ...]:
    return tuple(
        ProfileField(label, value)
        for label, key in pairs
        if (value := _optional_value(char, key)) and value != "未知"
    )


def build_character_profile(char: Mapping) -> CharacterProfile:
    """把单个角色原始字典转换为 Wiki 展示模型，不依赖 Qt。"""
    name = _value(char, "name")
    if "/" in name:
        name = name.split("/", 1)[0]

    identity = _non_empty_fields(char, [
        ("ID", "raw_id"),
        ("星级", "star"),
        ("职业", "profession"),
        ("属性", "element"),
        ("阵营", "faction"),
        ("生日", "birthday"),
        ("身高", "height"),
        ("CV", "cv"),
    ])

    primary_stats = tuple(
        ProfileStat(label, _value(char, initial, "-"), _value(char, maximum, "-"))
        for label, initial, maximum in [
            ("生命", "init_hp", "max_hp"),
            ("攻击", "init_atk", "max_atk"),
            ("防御", "init_def", "max_def"),
        ]
    )
    secondary_stats = tuple(
        ProfileField(label, value)
        for label, value in [
            ("暴击", _percent(char.get("crt"))),
            ("格挡", _percent(char.get("blk"))),
            ("暴击效果", _percent(char.get("crt_int"))),
            ("格挡效果", _percent(char.get("blk_int"))),
            ("移动速度", _value(char, "spd_move", "-")),
            ("攻击速度", _value(char, "spd_atk", "-")),
            ("攻击距离", _value(char, "range_atk", "-")),
            ("重量", _value(char, "weight", "-")),
        ]
    )

    skills: list[ProfileSkill] = []
    for label, field in [
        ("队长技能", "leader_skill"),
        ("普通技能", "normal_skill"),
        ("特殊技能", "special_skill"),
        ("爆发技能", "burst_skill"),
    ]:
        value = _optional_value(char, field)
        if value and value != "未知":
            skills.append(ProfileSkill(label, value))
    for prefix, label, limit in [
        ("passive_skill", "被动技能", 4),
        ("awakening_skill", "觉醒技能", 6),
    ]:
        for index in range(1, limit):
            value = _optional_value(char, f"{prefix}_{index}")
            if value and value != "未知":
                skills.append(ProfileSkill(f"{label} {index}", value))

    progression: list[ProfileField] = []
    for field, title, labels in [
        ("breakthrough_costs", "突破消耗", ["一", "二", "三", "四"]),
        ("normal_skill_upgrade_costs", "普通技能升级", ["一", "二", "三"]),
        ("passive_skill_upgrade_costs", "被动技能升级", ["一", "二", "三"]),
    ]:
        costs = char.get(field, []) or []
        for index, cost in enumerate(costs[:len(labels)]):
            if cost:
                progression.append(ProfileField(f"{title} · {labels[index]}", str(cost)))

    story = _non_empty_fields(char, [
        ("个人情报", "personal_info"),
        ("风闻", "anecdote"),
        ("记录", "record"),
        ("逸事", "anecdote2"),
    ])
    voices = tuple(
        ProfileField(label, value)
        for index, label in enumerate(VOICE_LABELS, start=1)
        if (value := _optional_value(char, f"voice_{index}"))
    )

    return CharacterProfile(
        name=name,
        raw_id=_value(char, "raw_id", "-"),
        summary=_optional_value(char, "description"),
        identity=identity,
        primary_stats=primary_stats,
        secondary_stats=secondary_stats,
        skills=tuple(skills),
        progression=tuple(progression),
        story=story,
        voices=voices,
        badge_info=_optional_value(char, "badge_info"),
    )
