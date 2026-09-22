import json
import logging
import os
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import List, Set

_logger = logging.getLogger(__name__)


def learn_group_from_event(event_obj, group_store: "GroupStore") -> None:
    """从事件载荷（dict 或带属性对象）学到群 openid 并登记。"""
    group_openid = None
    if isinstance(event_obj, dict):
        group_openid = event_obj.get("group_openid")
    else:
        group_openid = getattr(event_obj, "group_openid", None)
    if not group_openid:
        return
    # 当前官方事件（GROUP_AT_MESSAGE_CREATE / GROUP_ADD_ROBOT）均不携带群名，
    # name 字段预留，便于后续事件扩展或人工补录。
    group_store.add(openid=group_openid, name="")
    _logger.info("从群事件学到群 openid: %s", group_openid)


@dataclass(frozen=True)
class LearnedGroup:
    openid: str
    name: str = ""
    learned_at: str = ""


class GroupStore:
    """持久化机器人学到的群 openid，兼容旧版 group_openids 列表格式。"""

    def __init__(self, data_dir: str):
        self.data_dir = data_dir
        os.makedirs(data_dir, exist_ok=True)
        self.path = os.path.join(data_dir, "learned_groups.json")

    def load(self) -> List[LearnedGroup]:
        if not os.path.exists(self.path):
            return []
        try:
            with open(self.path, "r", encoding="utf-8") as f:
                data = json.load(f)
        except (json.JSONDecodeError, OSError):
            return []

        groups: List[LearnedGroup] = []
        for item in data.get("groups", []):
            groups.append(
                LearnedGroup(
                    openid=str(item.get("openid", "")),
                    name=str(item.get("name", "")),
                    learned_at=str(item.get("learned_at", "")),
                )
            )
        # 兼容旧版纯 openid 列表
        for gid in data.get("group_openids", []):
            groups.append(LearnedGroup(openid=str(gid)))
        return groups

    def openids(self) -> Set[str]:
        return {g.openid for g in self.load() if g.openid}

    def add(self, openid: str, name: str = "") -> None:
        groups = {g.openid: g for g in self.load()}
        if openid not in groups:
            groups[openid] = LearnedGroup(
                openid=openid,
                name=name,
                learned_at=datetime.now(timezone.utc).isoformat(),
            )
        self._save(list(groups.values()))

    def _save(self, groups: List[LearnedGroup]) -> None:
        data = {
            "groups": [
                {
                    "openid": g.openid,
                    "name": g.name,
                    "learned_at": g.learned_at,
                }
                for g in sorted(groups, key=lambda x: x.openid)
            ]
        }
        with open(self.path, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
