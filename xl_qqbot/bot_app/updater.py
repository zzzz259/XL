"""版本更新状态协同：服务器写状态文件，Bot 据此公告与静音。"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class UpdateStatus:
    updating: bool
    version: int
    new_characters: int = 0
    error: str | None = None


def read_update_status(path: str | Path) -> UpdateStatus | None:
    try:
        payload = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    if not isinstance(payload, dict) or "updating" not in payload:
        return None
    return UpdateStatus(
        updating=bool(payload.get("updating")),
        version=int(payload.get("version", 0)),
        new_characters=int(payload.get("new_characters", 0)),
        error=payload.get("error"),
    )


class ServiceMute:
    """更新期间的 @查询 静音标记，watcher 与事件客户端共享。"""

    def __init__(self) -> None:
        self.muted = False


class NoticeStateStore:
    """公告去重：记录已播报的事件行号，进程重启不重复播报。"""

    def __init__(self, data_dir: str | Path):
        self._path = Path(data_dir) / "update_notice_state.json"
        self._state = self._load()

    def _load(self) -> dict:
        try:
            return json.loads(self._path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return {}

    def _save(self) -> None:
        self._path.parent.mkdir(parents=True, exist_ok=True)
        temporary = self._path.with_suffix(".part")
        temporary.write_text(json.dumps(self._state, ensure_ascii=False), encoding="utf-8")
        temporary.replace(self._path)

    @property
    def consumed_events(self) -> int:
        return int(self._state.get("consumed_events", 0))

    def mark_consumed(self, count: int) -> None:
        self._state["consumed_events"] = count
        self._save()


@dataclass(frozen=True)
class UpdateEvent:
    event: str  # "start" | "finish"
    version: int
    new_characters: int = 0
    error: str | None = None


def read_new_events(path: str | Path, offset: int) -> tuple[list[UpdateEvent], int]:
    """从事件流水文件读取 offset 行之后的新事件，返回 (事件列表, 新行号)。"""
    events: list[UpdateEvent] = []
    try:
        lines = Path(path).read_text(encoding="utf-8").splitlines()
    except OSError:
        return events, offset
    for line in lines[offset:]:
        try:
            payload = json.loads(line)
        except ValueError:
            continue
        if not isinstance(payload, dict) or "event" not in payload:
            continue
        events.append(UpdateEvent(
            event=str(payload["event"]),
            version=int(payload.get("version", 0)),
            new_characters=int(payload.get("new_characters", 0)),
            error=payload.get("error"),
        ))
    return events, len(lines)
