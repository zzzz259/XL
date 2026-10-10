"""后台更新服务循环。"""

from __future__ import annotations

import threading
import time
import logging
from collections.abc import Callable
from datetime import datetime

from .config import ServerConfig
from .pipeline import UpdatePipeline
from .rerun_schedule import refresh_schedule_if_due
from .state import StateStore
from .update_coordinator import UpdateCoordinator

LOGGER = logging.getLogger(__name__)


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
        last_schedule_check = 0.0
        while not stop_event.is_set():
            now = self.clock()
            self.coordinator.run_scheduled_if_due(now)
            schedule_check_interval = min(
                60,
                max(1, getattr(self.config, "rerun_schedule_refresh_seconds", 3600)),
            )
            monotonic_now = time.monotonic()
            if (
                getattr(self.config, "rerun_schedule_enabled", True)
                and monotonic_now - last_schedule_check >= schedule_check_interval
            ):
                last_schedule_check = monotonic_now
                try:
                    refreshed = refresh_schedule_if_due(
                        self.config.data_dir / "rerun_schedule",
                        as_of=now,
                        refresh_seconds=getattr(self.config, "rerun_schedule_refresh_seconds", 3600),
                        node_bin=self.config.node_bin,
                        forecast_limit=getattr(self.config, "rerun_schedule_forecast_limit", 20),
                    )
                    if refreshed:
                        LOGGER.info("stage=rerun_render status=refreshed reason=clock_or_interval")
                except (OSError, ValueError, KeyError, TypeError, RuntimeError):
                    # Schedule availability must not stop the game-update monitor.
                    LOGGER.exception("stage=rerun_render status=failed fallback=last_known_good")
            self.sleeper(1.0)
