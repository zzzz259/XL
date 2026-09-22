"""普通轮询与每 21 天一次的密集检查调度。"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta

from .state import ScheduleState


@dataclass(frozen=True)
class PollScheduler:
    normal_interval_seconds: int
    burst_interval_seconds: int
    burst_duration_seconds: int
    burst_anchor: datetime

    def _burst_start(self, now: datetime) -> datetime | None:
        if now < self.burst_anchor:
            return None
        cycles = (now - self.burst_anchor).days // 21
        return self.burst_anchor + timedelta(days=cycles * 21)

    def is_burst_time(self, now: datetime) -> bool:
        start = self._burst_start(now)
        return bool(start and start <= now < start + timedelta(seconds=self.burst_duration_seconds))

    def next_due(self, now: datetime, state: ScheduleState) -> datetime:
        burst_start = state.burst_started_at or self._burst_start(now)
        if burst_start and burst_start <= now < burst_start + timedelta(seconds=self.burst_duration_seconds):
            interval = self.burst_interval_seconds
        else:
            interval = self.normal_interval_seconds
        if state.last_check is None:
            return now
        return state.last_check + timedelta(seconds=interval)

    def record_check(
        self,
        state: ScheduleState,
        checked_at: datetime,
        version_timestamp: int | None,
        processed: bool,
    ) -> ScheduleState:
        state.last_check = checked_at
        if version_timestamp is not None:
            state.last_version_timestamp = version_timestamp
        if processed:
            state.burst_started_at = None
            state.pending_version_timestamp = None
        elif self.is_burst_time(checked_at):
            state.burst_started_at = self._burst_start(checked_at)
        else:
            state.burst_started_at = None
        return state
