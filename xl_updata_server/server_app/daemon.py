"""后台更新服务循环。"""

from __future__ import annotations

import threading
import time
from collections.abc import Callable
from datetime import datetime

from .config import ServerConfig
from .pipeline import UpdatePipeline
from .state import StateStore
from .update_coordinator import UpdateCoordinator

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
        coordinator: UpdateCoordinator | None = None,
    ):
        self.config = config
        self.pipeline = pipeline
        self.state_store = state_store
        self.clock = clock or (lambda: datetime.now().astimezone())
        self.sleeper = sleeper or time.sleep
        self.coordinator = coordinator or UpdateCoordinator(
            config, pipeline, state_store, clock=self.clock
        )

    def run(self, stop_event: threading.Event) -> None:
        while not stop_event.is_set():
            now = self.clock()
            self.coordinator.run_scheduled_if_due(now)
            self.sleeper(1.0)
