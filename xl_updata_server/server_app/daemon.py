"""后台更新服务循环。"""

from __future__ import annotations

import threading
import time
from collections.abc import Callable
from datetime import datetime

from .config import ServerConfig
from .pipeline import UpdatePipeline
from .scheduler import PollScheduler
from .state import StateStore


def should_poll(now: datetime, last_check: datetime | None, burst: bool, interval_seconds: int) -> bool:
    if last_check is None:
        return True
    return (now - last_check).total_seconds() >= interval_seconds


class UpdateDaemon:
    def __init__(
        self,
        config: ServerConfig,
        pipeline: UpdatePipeline,
        state_store: StateStore,
        clock: Callable[[], datetime] | None = None,
        sleeper: Callable[[float], None] | None = None,
    ):
        self.config = config
        self.pipeline = pipeline
        self.state_store = state_store
        self.clock = clock or (lambda: datetime.now().astimezone())
        self.sleeper = sleeper or time.sleep

    def run(self, stop_event: threading.Event) -> None:
        scheduler = PollScheduler(
            self.config.normal_interval_seconds,
            self.config.burst_interval_seconds,
            self.config.burst_duration_seconds,
            self.config.burst_anchor,
        )
        state = self.state_store.load()
        while not stop_event.is_set():
            now = self.clock()
            burst = scheduler.is_burst_time(now) or bool(state.burst_started_at)
            interval = self.config.burst_interval_seconds if burst else self.config.normal_interval_seconds
            if should_poll(now, state.last_check, burst, interval):
                result = self.pipeline.run_once()
                state = scheduler.record_check(state, self.clock(), result.version_timestamp, result.processed)
                if result.version_timestamp is not None and not result.processed:
                    state.pending_version_timestamp = result.version_timestamp
                self.state_store.save(state)
            self.sleeper(1.0)
