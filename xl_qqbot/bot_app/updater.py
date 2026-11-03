"""版本更新状态协同：服务器写状态文件，Bot 据此公告与静音。"""

from __future__ import annotations

import hashlib
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
        temporary.write_text(
            json.dumps(self._state, ensure_ascii=False), encoding="utf-8"
        )
        temporary.replace(self._path)

    def quarantine(self, event):
        records = self._state.setdefault("quarantines", [])
        records.append({"line": event.line_number, "kind": event.event,
                        "hash": event.line_hash, "reason": "unknown lifecycle kind"})
        self._state["quarantines"] = records[-100:]
        self._save()

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
    line_number: int = 0
    run_id: str | None = None
    line_hash: str = ""


def _number(value):
    if type(value) is int and 0 <= value < 2**63:
        return value
    if isinstance(value, str) and len(value) <= 19 and value.isascii() and value.isdigit():
        number = int(value)
        if number < 2**63:
            return number
    raise ValueError("invalid lifecycle number")


def read_new_events(path: str | Path, offset: int, *, limit: int | None = 128) -> tuple[list[UpdateEvent], int]:
    """Bounded physical-line paging; malformed lines retain consumed-line semantics."""
    if limit is not None and (type(limit) is not int or not 1 <= limit <= 128):
        raise ValueError("invalid lifecycle page limit")
    events = []
    consumed = offset
    try:
        with Path(path).open(encoding="utf-8") as stream:
            for _ in range(offset):
                skipped = stream.readline(65537)
                if not skipped:
                    return events, offset
                while skipped and not skipped.endswith("\n"):
                    skipped = stream.readline(65537)
            while limit is None or consumed - offset < limit:
                line = stream.readline(65537)
                if not line:
                    break
                # Drain an oversized physical line without retaining its content.
                oversized = len(line) > 65536
                if oversized:
                    while line and not line.endswith("\n"):
                        line = stream.readline(65537)
                consumed += 1
                if oversized:
                    continue
                try:
                    payload = json.loads(line)
                    if not isinstance(payload, dict) or "event" not in payload:
                        continue
                    version = _number(payload.get("version", 0))
                    count = _number(payload.get("new_characters", 0))
                    kind = payload["event"]
                    run = payload.get("run_id")
                    if not isinstance(kind, str) or not 0 < len(kind) <= 128:
                        continue
                    if run is not None and (not isinstance(run, str) or not 0 < len(run) <= 256):
                        continue
                except (ValueError, RecursionError):
                    continue
                events.append(UpdateEvent(kind, version, count, payload.get("error"), consumed,
                                          run, hashlib.sha256(line.encode()).hexdigest()))
    except OSError:
        return events, offset
    return events, consumed
