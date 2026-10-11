"""Serialize scheduled and authenticated manual game-update runs."""

from __future__ import annotations

import threading
import uuid
from collections.abc import Callable
from datetime import datetime

from .config import ServerConfig
from .pipeline import ProcessResult, UpdatePipeline
from .scheduler import PollScheduler
from .state import ScheduleState, StateStore, UpdateJobStore


class UpdateCoordinator:
    def __init__(
        self,
        config: ServerConfig,
        pipeline: UpdatePipeline,
        state_store: StateStore,
        *,
        job_store: UpdateJobStore | None = None,
        clock: Callable[[], datetime] | None = None,
    ):
        self.config = config
        self.pipeline = pipeline
        self.state_store = state_store
        self.job_store = job_store or UpdateJobStore(state_store.path)
        self.clock = clock or (lambda: datetime.now().astimezone())
        self.scheduler = PollScheduler(
            config.normal_interval_seconds,
            config.burst_interval_seconds,
            config.burst_duration_seconds,
            config.burst_anchor,
        )
        self._run_lock = threading.Lock()
        self._metadata_lock = threading.Lock()
        self._idle = threading.Event()
        self._idle.set()
        self._accepting = True
        self._active_job_id: str | None = None
        self._active_source: str | None = None
        self._last_result: dict | None = None
        self.job_store.mark_incomplete_interrupted(self.clock())

    def run_scheduled_if_due(self, now: datetime) -> ProcessResult | None:
        if not self.config.cdn_poll_enabled:
            return None
        state = self.state_store.load()
        burst = self.scheduler.is_burst_time(now) or bool(state.burst_started_at)
        interval = (
            self.config.burst_interval_seconds
            if burst
            else self.config.normal_interval_seconds
        )
        last_check = state.last_check
        if last_check is not None and (now - last_check).total_seconds() < interval:
            return None
        if not self._run_lock.acquire(blocking=False):
            return None
        job_id = str(uuid.uuid4())
        with self._metadata_lock:
            self._idle.clear()
            self._active_job_id = job_id
            self._active_source = "scheduled"
        try:
            self.job_store.create(job_id, "scheduled", now)
            self.job_store.mark_running(job_id, now)
            result = self._execute_locked("scheduled", job_id)
            self.job_store.finish(
                job_id,
                "failed" if result.error else "succeeded",
                self.clock(),
                _result_fields(result),
            )
            return result
        except Exception:
            try:
                self.job_store.finish(
                    job_id, "failed", self.clock(), {"error": "update job failed"}
                )
            except Exception:
                pass
            raise
        finally:
            self._finish_run()

    def enqueue_manual(self) -> str | None:
        with self._metadata_lock:
            if not self._accepting or not self._run_lock.acquire(blocking=False):
                return None
            job_id = str(uuid.uuid4())
            now = self.clock()
            try:
                self.job_store.create(job_id, "manual", now)
            except Exception:
                self._run_lock.release()
                raise
            self._idle.clear()
            self._active_job_id = job_id
            self._active_source = "manual"
            worker = threading.Thread(
                target=self._run_manual,
                args=(job_id,),
                name=f"update-job-{job_id[:8]}",
                daemon=False,
            )
            try:
                worker.start()
            except Exception:
                self.job_store.finish(
                    job_id,
                    "failed",
                    self.clock(),
                    {"error": "worker could not start"},
                )
                self._active_job_id = None
                self._active_source = None
                self._idle.set()
                self._run_lock.release()
                raise
            return job_id

    def _run_manual(self, job_id: str) -> None:
        try:
            now = self.clock()
            self.job_store.mark_running(job_id, now)
            result = self._execute_locked("manual", job_id)
            status = "failed" if result.error else "succeeded"
            self.job_store.finish(
                job_id,
                status,
                self.clock(),
                _result_fields(result),
            )
        except Exception:
            self.job_store.finish(
                job_id,
                "failed",
                self.clock(),
                {"error": "update job failed"},
            )
        finally:
            self._finish_run()

    def _execute_locked(self, source: str, job_id: str | None) -> ProcessResult:
        result = self.pipeline.run_once()
        checked_at = self.clock()
        state: ScheduleState = self.state_store.load()
        state = self.scheduler.record_check(
            state,
            checked_at,
            result.version_timestamp,
            result.processed,
        )
        if result.version_timestamp is not None and not result.processed:
            state.pending_version_timestamp = result.version_timestamp
        self.state_store.save(state)
        with self._metadata_lock:
            self._last_result = {
                "source": source,
                "job_id": job_id,
                "status": "failed" if result.error else "succeeded",
                "checked_at": checked_at.isoformat(),
                **_result_fields(result),
            }
        return result

    def _finish_run(self) -> None:
        with self._metadata_lock:
            self._active_job_id = None
            self._active_source = None
            self._idle.set()
        self._run_lock.release()

    def status(self) -> dict:
        with self._metadata_lock:
            snapshot = {
                "environment": self.config.environment,
                "cdn_poll_enabled": self.config.cdn_poll_enabled,
                "running": not self._idle.is_set(),
                "active_job_id": self._active_job_id,
                "active_source": self._active_source,
                "accepting": self._accepting,
                "last_run": dict(self._last_result) if self._last_result else None,
            }
        if snapshot["last_run"] is None:
            latest = self.job_store.latest()
            if latest is not None:
                snapshot["last_run"] = {
                    "source": latest["trigger_source"],
                    "job_id": latest["job_id"],
                    "status": latest["status"],
                    "checked_at": latest["finished_at"],
                    **(latest["result"] or {}),
                }
        return snapshot

    def get_job(self, job_id: str) -> dict | None:
        return self.job_store.get(job_id)

    def stop_accepting(self) -> None:
        with self._metadata_lock:
            self._accepting = False

    def wait_idle(self, timeout: float | None = None) -> bool:
        return self._idle.wait(timeout)


def _result_fields(result: ProcessResult) -> dict:
    return {
        "version_timestamp": result.version_timestamp,
        "processed": result.processed,
        "character_count": result.character_count,
        "skin_count": result.skin_count,
        "card_count": result.card_count,
        "new_character_count": result.new_character_count,
        "updated_character_count": result.updated_character_count,
        "warnings": list(result.warnings[:5]),
        "error": "update failed" if result.error else None,
    }
