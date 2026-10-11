"""服务器调度状态的 SQLite 持久化。"""

from __future__ import annotations

import json
import sqlite3
from contextlib import closing
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
        with closing(sqlite3.connect(self.path)) as connection:
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
        with closing(sqlite3.connect(self.path)) as connection, connection:
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


class UpdateJobStore:
    """持久化手动触发的更新任务，不保存请求头或凭据。"""

    _RESULT_FIELDS = frozenset(
        {
            "version_timestamp",
            "processed",
            "character_count",
            "skin_count",
            "card_count",
            "new_character_count",
            "updated_character_count",
            "warnings",
        }
    )
    _TERMINAL = frozenset({"succeeded", "failed", "interrupted"})

    def __init__(self, path: str | Path):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._ensure_table()

    def _ensure_table(self) -> None:
        with closing(sqlite3.connect(self.path)) as connection, connection:
            connection.execute(
                """CREATE TABLE IF NOT EXISTS update_jobs (
                    job_id TEXT PRIMARY KEY,
                    trigger_source TEXT NOT NULL,
                    status TEXT NOT NULL CHECK (status IN
                        ('queued', 'running', 'succeeded', 'failed', 'interrupted')),
                    created_at TEXT NOT NULL,
                    started_at TEXT,
                    finished_at TEXT,
                    result_json TEXT
                )"""
            )

    def create(self, job_id: str, trigger_source: str, created_at: datetime) -> None:
        if not job_id or trigger_source not in {"manual", "scheduled"}:
            raise ValueError("invalid update job identity")
        self._ensure_table()
        with closing(sqlite3.connect(self.path)) as connection, connection:
            connection.execute(
                "INSERT INTO update_jobs(job_id, trigger_source, status, created_at) "
                "VALUES (?, ?, 'queued', ?)",
                (job_id, trigger_source, created_at.isoformat()),
            )

    def mark_running(self, job_id: str, started_at: datetime) -> None:
        self._transition(job_id, "running", started_at=started_at)

    def finish(
        self,
        job_id: str,
        status: str,
        finished_at: datetime,
        result: dict,
    ) -> None:
        if status not in self._TERMINAL:
            raise ValueError("update job finish status must be terminal")
        safe_result = {}
        for key, value in result.items():
            if key not in self._RESULT_FIELDS:
                continue
            if key == "warnings":
                if isinstance(value, (list, tuple)):
                    warnings = [" ".join(item.split())[:500] for item in value if isinstance(item, str)][:5]
                    warnings = [item for item in warnings if item]
                    if warnings:
                        safe_result[key] = warnings
            elif isinstance(value, (str, int, float, bool, type(None))):
                safe_result[key] = value
        if result.get("error"):
            safe_result["error"] = "update failed"
        encoded = json.dumps(safe_result, ensure_ascii=False, separators=(",", ":"))
        self._transition(job_id, status, finished_at=finished_at, result_json=encoded)

    def _transition(
        self,
        job_id: str,
        status: str,
        *,
        started_at: datetime | None = None,
        finished_at: datetime | None = None,
        result_json: str | None = None,
    ) -> None:
        assignments = ["status = ?"]
        values: list[object] = [status]
        for column, value in (
            ("started_at", started_at),
            ("finished_at", finished_at),
            ("result_json", result_json),
        ):
            if value is not None:
                assignments.append(f"{column} = ?")
                values.append(value.isoformat() if isinstance(value, datetime) else value)
        values.append(job_id)
        with closing(sqlite3.connect(self.path)) as connection, connection:
            cursor = connection.execute(
                f"UPDATE update_jobs SET {', '.join(assignments)} WHERE job_id = ?",
                values,
            )
            if cursor.rowcount != 1:
                raise KeyError("unknown update job")

    def get(self, job_id: str) -> dict | None:
        self._ensure_table()
        with closing(sqlite3.connect(self.path)) as connection:
            connection.row_factory = sqlite3.Row
            row = connection.execute(
                "SELECT job_id, trigger_source, status, created_at, started_at, finished_at, result_json "
                "FROM update_jobs WHERE job_id = ?",
                (job_id,),
            ).fetchone()
        if row is None:
            return None
        return {
            "job_id": row["job_id"],
            "trigger_source": row["trigger_source"],
            "status": row["status"],
            "created_at": row["created_at"],
            "started_at": row["started_at"],
            "finished_at": row["finished_at"],
            "result": json.loads(row["result_json"]) if row["result_json"] else None,
        }

    def latest(self) -> dict | None:
        self._ensure_table()
        with closing(sqlite3.connect(self.path)) as connection:
            row = connection.execute(
                "SELECT job_id FROM update_jobs ORDER BY created_at DESC, rowid DESC LIMIT 1"
            ).fetchone()
        return self.get(row[0]) if row else None

    def mark_incomplete_interrupted(self, finished_at: datetime) -> None:
        self._ensure_table()
        encoded = json.dumps({"error": "process restarted"}, separators=(",", ":"))
        with closing(sqlite3.connect(self.path)) as connection, connection:
            connection.execute(
                "UPDATE update_jobs SET status='interrupted', finished_at=?, result_json=? "
                "WHERE status IN ('queued', 'running')",
                (finished_at.isoformat(), encoded),
            )
