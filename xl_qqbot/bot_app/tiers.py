"""把已配置群映射到运行环境，用于 shared router 转发和来源隔离。"""

from __future__ import annotations

from .config import GroupsConfig

TIER_PRODUCTION = "production"
TIER_TEST = "test"
TIER_DEBUG = "debug"

TIER_RANK = {TIER_PRODUCTION: 0, TIER_TEST: 1, TIER_DEBUG: 2}

VALID_TIERS = tuple(TIER_RANK.keys())


class GroupTier:
    """群归属分类。未配置的群归为 production。"""

    def __init__(self, groups: GroupsConfig | None = None):
        groups = groups or GroupsConfig()
        self._debug = frozenset(groups.debug)
        self._test = frozenset(groups.test)

    def tier_of(self, group_openid) -> str:
        """群级别：debug 列表优先，其次 test，未配置即 production。"""
        group = str(group_openid)
        if group in self._debug:
            return TIER_DEBUG
        if group in self._test:
            return TIER_TEST
        return TIER_PRODUCTION
