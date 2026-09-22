"""候选选择状态：键 (group_openid, member_openid) 防串台，TTL 120 秒。"""

from __future__ import annotations

import time
from dataclasses import dataclass, field

TTL_SECONDS = 120


@dataclass
class PendingSelection:
    query: str  # 用户原始的查询词（学习用，已清洗）
    options: list  # Match 候选列表（不含"都不是"项）
    created_at: float = field(default_factory=time.time)

    def expired(self) -> bool:
        return time.time() - self.created_at > TTL_SECONDS


class SelectionStore:
    """同群不同用户互不影响；新查询覆盖旧待选择；过期即清除。"""

    def __init__(self) -> None:
        self._pending: dict[tuple[str, str], PendingSelection] = {}

    def set(self, group_openid: str, member_openid: str, query: str, options: list) -> None:
        self._pending[(group_openid, member_openid)] = PendingSelection(
            query=query, options=list(options)
        )

    def get(self, group_openid: str, member_openid: str) -> PendingSelection | None:
        pending = self._pending.get((group_openid, member_openid))
        if pending is None:
            return None
        if pending.expired():
            self._pending.pop((group_openid, member_openid), None)
            return None
        return pending

    def clear(self, group_openid: str, member_openid: str) -> None:
        self._pending.pop((group_openid, member_openid), None)

    def choose(self, group_openid: str, member_openid: str, digit: str) -> tuple[str, object]:
        """返回 (status, payload)：picked(返回 Match)/none/invalid/no_pending。

        选择成功（含"都不是"）会清除状态；invalid 保留待选择。
        """
        pending = self.get(group_openid, member_openid)
        if pending is None:
            return "no_pending", None
        try:
            choice = int(str(digit).strip())
        except ValueError:
            return "invalid", None
        if choice < 1 or choice > len(pending.options) + 1:
            return "invalid", None
        self.clear(group_openid, member_openid)
        if choice == len(pending.options) + 1:
            return "none", None
        return "picked", pending.options[choice - 1]

    def __len__(self) -> int:
        return len(self._pending)
