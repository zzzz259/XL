"""服务器调度状态的 SQLite 持久化。"""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path


@dataclass
class ScheduleState:
    last_check: datetime | None = None
    last_version_timestamp: int | None = None
    burst_started_at: datetime | None = None
    pending_version_timestamp: int | None = None


def _encode(value: datetime | None) -> str | None:
    return value.isoformat() if value else None


def _decode(value: str | None) -> datetime | None:
    return datetime.fromisoformat(value) if value else None


class StateStore:
    def __init__(self, path: str | Path):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)

    def load(self) -> ScheduleState:
        with sqlite3.connect(self.path) as connection:
            connection.execute(
                """CREATE TABLE IF NOT EXISTS service_state (
                    id INTEGER PRIMARY KEY CHECK (id = 1),
                    last_check TEXT,
                    last_version_timestamp INTEGER,
                    burst_started_at TEXT,
                    pending_version_timestamp INTEGER
                )"""
            )
            row = connection.execute(
                "SELECT last_check, last_version_timestamp, burst_started_at, pending_version_timestamp "
                "FROM service_state WHERE id=1"
            ).fetchone()
        if row is None:
            return ScheduleState()
        return ScheduleState(_decode(row[0]), row[1], _decode(row[2]), row[3])

    def save(self, state: ScheduleState) -> None:
        with sqlite3.connect(self.path) as connection:
            connection.execute(
                """CREATE TABLE IF NOT EXISTS service_state (
                    id INTEGER PRIMARY KEY CHECK (id = 1),
                    last_check TEXT,
                    last_version_timestamp INTEGER,
                    burst_started_at TEXT,
                    pending_version_timestamp INTEGER
                )"""
            )
            connection.execute(
                """INSERT INTO service_state
                    (id, last_check, last_version_timestamp, burst_started_at, pending_version_timestamp)
                    VALUES (1, ?, ?, ?, ?)
                    ON CONFLICT(id) DO UPDATE SET
                        last_check=excluded.last_check,
                        last_version_timestamp=excluded.last_version_timestamp,
                        burst_started_at=excluded.burst_started_at,
                        pending_version_timestamp=excluded.pending_version_timestamp
                """,
                (
                    _encode(state.last_check),
                    state.last_version_timestamp,
                    _encode(state.burst_started_at),
                    state.pending_version_timestamp,
                ),
            )
