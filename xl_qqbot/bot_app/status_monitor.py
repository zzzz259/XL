"""Pluggable, privacy-preserving game server status sampling for the router."""

from __future__ import annotations

import asyncio
import inspect
import logging
import sqlite3
import time
from collections.abc import Awaitable, Callable, Iterable
from contextlib import contextmanager
from dataclasses import dataclass
from enum import Enum
from pathlib import Path

from .proactive_outbox import ProactiveOutbox

_logger = logging.getLogger(__name__)


class Status(str, Enum):
    MAINTENANCE_CONFIRMED = "MAINTENANCE_CONFIRMED"
    OPEN_CANDIDATE_GATEWAY_REACHABLE = "OPEN_CANDIDATE_GATEWAY_REACHABLE"
    UNKNOWN = "UNKNOWN"


@dataclass(frozen=True)
class StatusSample:
    """Sanitized fields needed to classify a probe result; never store secrets."""

    accepted: bool
    ret_code: int | None
    maintain_state_present: bool
    maintain_state: object
    gateway_reachable: bool
    error_class: str | None = None
    observed_at: float = 0.0

    def __post_init__(self) -> None:
        if not self.observed_at:
            object.__setattr__(self, "observed_at", time.time())


def classify_sample(sample: StatusSample) -> Status:
    """Classify login-gate evidence; this does not establish actual gameplay."""
    if (
        type(sample.accepted) is not bool
        or type(sample.maintain_state_present) is not bool
        or type(sample.gateway_reachable) is not bool
    ):
        return Status.UNKNOWN
    if (
        not sample.accepted
        or type(sample.ret_code) is not int
        or sample.ret_code != 0
    ):
        return Status.UNKNOWN
    if sample.maintain_state_present:
        value = sample.maintain_state
        if type(value) is not int or value not in (0, 1):
            return Status.UNKNOWN
        if value == 1:
            return Status.MAINTENANCE_CONFIRMED
    if sample.gateway_reachable:
        return Status.OPEN_CANDIDATE_GATEWAY_REACHABLE
    return Status.UNKNOWN


class StatusHistory:
    """Sanitized status history, kept separate from the proactive message DB."""

    def __init__(self, path: str | Path):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self._connect() as connection:
            connection.execute(
                """CREATE TABLE IF NOT EXISTS samples (
                    sequence INTEGER PRIMARY KEY AUTOINCREMENT,
                    observed_at REAL NOT NULL,
                    status TEXT NOT NULL,
                    accepted INTEGER NOT NULL,
                    ret_code INTEGER,
                    maintain_state_present INTEGER NOT NULL,
                    maintain_state_type TEXT NOT NULL,
                    gateway_reachable INTEGER NOT NULL,
                    error_class TEXT
                )"""
            )
            connection.execute(
                """CREATE TABLE IF NOT EXISTS transitions (
                    event_key TEXT PRIMARY KEY,
                    previous_status TEXT NOT NULL,
                    current_status TEXT NOT NULL,
                    observed_at REAL NOT NULL
                )"""
            )

    def record_sample(self, sample: StatusSample, status: Status) -> None:
        state_type = type(sample.maintain_state).__name__
        if state_type not in {"NoneType", "int", "bool", "str", "float"}:
            state_type = "other"
        ret_code = sample.ret_code
        if isinstance(ret_code, bool) or not isinstance(ret_code, int):
            ret_code = None
        with self._connect() as connection:
            connection.execute(
                """INSERT INTO samples (
                    observed_at, status, accepted, ret_code,
                    maintain_state_present, maintain_state_type,
                    gateway_reachable, error_class
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    sample.observed_at,
                    status.value,
                    int(sample.accepted is True),
                    ret_code,
                    int(sample.maintain_state_present is True),
                    state_type,
                    int(sample.gateway_reachable is True),
                    self._safe_error_class(sample.error_class),
                ),
            )

    @staticmethod
    def _safe_error_class(error_class: str | None) -> str | None:
        allowed = {"TIMEOUT", "NETWORK_ERROR", "AUTH_ERROR", "PARSE_ERROR", "PROBE_ERROR"}
        return error_class if error_class in allowed else ("PROBE_ERROR" if error_class else None)

    def record_transition(
        self, event_key: str, previous: Status, current: Status, observed_at: float
    ) -> None:
        with self._connect() as connection:
            connection.execute(
                """INSERT OR IGNORE INTO transitions (
                    event_key, previous_status, current_status, observed_at
                ) VALUES (?, ?, ?, ?)""",
                (event_key, previous.value, current.value, observed_at),
            )

    def sample_count(self) -> int:
        with self._connect() as connection:
            return int(connection.execute("SELECT COUNT(*) FROM samples").fetchone()[0])

    def transition_count(self) -> int:
        with self._connect() as connection:
            return int(connection.execute("SELECT COUNT(*) FROM transitions").fetchone()[0])

    @contextmanager
    def _connect(self):
        connection = sqlite3.connect(self.path, timeout=30)
        connection.execute("PRAGMA busy_timeout = 30000")
        try:
            yield connection
        except Exception:
            connection.rollback()
            raise
        else:
            connection.commit()
        finally:
            connection.close()


Probe = Callable[[], StatusSample | Awaitable[StatusSample]]
Sleeper = Callable[[float], Awaitable[None]]


class StatusMonitor:
    """Serial sampler with fast confirmation before a transition is broadcast."""

    def __init__(
        self,
        probe: Probe,
        history: StatusHistory,
        outbox: ProactiveOutbox | None,
        recipients: Iterable[str],
        *,
        interval_seconds: float = 30,
        confirm_delay_seconds: float = 3,
        sleeper: Sleeper = asyncio.sleep,
        notification_client=None,
    ):
        if interval_seconds <= 0 or confirm_delay_seconds <= 0:
            raise ValueError("sampling intervals must be positive")
        self.probe = probe
        self.history = history
        self.outbox = outbox
        self.notification_client = notification_client
        self.recipients = tuple(dict.fromkeys(r for r in recipients if r))
        self.interval_seconds = interval_seconds
        self.confirm_delay_seconds = confirm_delay_seconds
        self.sleeper = sleeper
        self.confirmed_state: Status | None = None

    def set_confirmed_state(self, state: Status | None) -> None:
        """Set prior state for controlled startup and deterministic tests."""
        self.confirmed_state = state

    async def _sample(self) -> tuple[StatusSample, Status]:
        try:
            result = self.probe()
            sample = await result if inspect.isawaitable(result) else result
        except asyncio.CancelledError:
            raise
        except TimeoutError:
            sample = StatusSample(False, None, False, None, False, "TIMEOUT")
        except ConnectionError:
            sample = StatusSample(False, None, False, None, False, "NETWORK_ERROR")
        except Exception:
            sample = StatusSample(False, None, False, None, False, "PROBE_ERROR")
        if not isinstance(sample, StatusSample):
            sample = StatusSample(False, None, False, None, False, "PARSE_ERROR")
        status = classify_sample(sample)
        self.history.record_sample(sample, status)
        return sample, status

    async def observe_once(self) -> bool:
        first, candidate = await self._sample()
        if candidate is Status.UNKNOWN:
            return False
        if self.confirmed_state is None:
            self.confirmed_state = candidate
            return False
        if candidate is self.confirmed_state:
            return False

        await self.sleeper(self.confirm_delay_seconds)
        _, confirmed_candidate = await self._sample()
        if confirmed_candidate is not candidate:
            return False

        previous = self.confirmed_state
        event_key = (
            f"game-status:{previous.value}:{candidate.value}:"
            f"{int(first.observed_at * 1000)}"
        )
        self.history.record_transition(event_key, previous, candidate, first.observed_at)
        message = self._message(candidate)
        if self.notification_client is not None:
            await self.notification_client.enqueue(
                event_key=event_key,
                tier="test",
                messages=[
                    {
                        "recipient": recipient,
                        "ordinal": ordinal,
                        "kind": "text",
                        "text": message,
                    }
                    for ordinal, recipient in enumerate(self.recipients)
                ],
            )
        elif self.outbox is not None:
            for ordinal, recipient in enumerate(self.recipients):
                self.outbox.enqueue_text(event_key, recipient, ordinal, message)
        else:
            raise RuntimeError("status monitor has no notification sink")
        self.confirmed_state = candidate
        return True

    async def run(self, stop_event: asyncio.Event) -> None:
        """Run a single serial sampler; slow runs skip missed ticks without overlap."""
        loop = asyncio.get_running_loop()
        next_start = loop.time()
        while not stop_event.is_set():
            try:
                await self.observe_once()
            except asyncio.CancelledError:
                raise
            except Exception as error:
                _logger.warning("服务器状态采样失败 error=%s", type(error).__name__)
            next_start += self.interval_seconds
            if next_start <= loop.time():
                next_start = loop.time() + self.interval_seconds
            try:
                await asyncio.wait_for(
                    stop_event.wait(), timeout=max(0.0, next_start - loop.time())
                )
            except asyncio.TimeoutError:
                pass

    @staticmethod
    def _message(status: Status) -> str:
        if status is Status.MAINTENANCE_CONFIRMED:
            return "检测到游戏登录预检返回维护状态（maintainState=1）。"
        return "游戏登录预检未命中维护拦截，且官方网关可连接；游戏内可玩状态未由本探针验证。"
