"""群分级 + 功能门禁：功能级别 L 在群级别 G 开放 ⟺ G >= L。

群三级：production（默认，未配置群）< test < debug（累积语义：调试群全开放）。
功能级别配置在 [features]（如 character_query = "debug"），缺省 production。
tiers 实例每次现算，配置变更（如群从 debug 列表移出）立即生效。
"""

from __future__ import annotations

from typing import Dict, List

from .config import GroupsConfig

TIER_PRODUCTION = "production"
TIER_TEST = "test"
TIER_DEBUG = "debug"

# 级别数值：群级别 >= 功能级别即开放
TIER_RANK = {TIER_PRODUCTION: 0, TIER_TEST: 1, TIER_DEBUG: 2}

VALID_TIERS = tuple(TIER_RANK.keys())


class GroupTier:
    """群分级查询与功能门禁判定。无配置时全部 production（全开放，不回归）。"""

    def __init__(self, groups: GroupsConfig | None = None):
        groups = groups or GroupsConfig()
        self._debug = frozenset(groups.debug)
        self._test = frozenset(groups.test)
        self._features: Dict[str, str] = dict(groups.features)

    def tier_of(self, group_openid) -> str:
        """群级别：debug 列表优先，其次 test，未配置即 production。"""
        group = str(group_openid)
        if group in self._debug:
            return TIER_DEBUG
        if group in self._test:
            return TIER_TEST
        return TIER_PRODUCTION

    def level_of(self, feature: str) -> str:
        """功能所需级别；未知功能缺省 production（现有行为）。"""
        return self._features.get(str(feature), TIER_PRODUCTION)

    def available(self, feature: str, group_openid) -> bool:
        """累积语义：群级别 >= 功能级别即开放。"""
        return TIER_RANK[self.tier_of(group_openid)] >= TIER_RANK[self.level_of(feature)]

    def filter_groups(self, feature: str, group_openids) -> List[str]:
        """按功能级别过滤目标群（推送用），保持原顺序。"""
        return [g for g in group_openids if self.available(feature, g)]
