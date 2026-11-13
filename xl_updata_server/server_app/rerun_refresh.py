"""Refresh the local rerun view without invoking a game-resource update."""
from __future__ import annotations

import hashlib
import json
import logging
from datetime import datetime, timedelta
from pathlib import Path

from .rerun_bootstrap import load_bootstrap_calendar
from .rerun_calendar import SHANGHAI
from .rerun_schedule import (
    _write_atomic,
    project_calendar,
    publish_schedule_snapshot,
    read_current_schedule,
    render_schedule_png,
)

LOGGER = logging.getLogger(__name__)
STATE_FILE = 'refresh-state.json'


def refresh_day(now: datetime) -> str:
    if now.tzinfo is None or now.utcoffset() is None:
        raise ValueError('refresh time must be timezone-aware')
    return (now.astimezone(SHANGHAI) - timedelta(hours=5)).date().isoformat()


def view_digest(render_model: dict) -> str:
    raw = json.dumps({'renderer_model': 2, 'render': render_model},
                     ensure_ascii=False, sort_keys=True, separators=(',', ':')).encode('utf-8')
    return hashlib.sha256(raw).hexdigest()


class RerunScheduleRefresher:
    def __init__(self, schedule_dir: str | Path, *, node_bin: str = 'node',
                 forecast_limit: int = 20, previous_schedule_dir: str | Path | None = None,
                 bootstrap_config=None):
        self.root = Path(schedule_dir)
        self.node_bin = node_bin
        self.forecast_limit = forecast_limit
        self.previous_root = Path(previous_schedule_dir) if previous_schedule_dir else None
        self.bootstrap_config = bootstrap_config
        self.reload_state()

    def reload_state(self) -> None:
        """Read only at startup or after a game update has published new inputs."""
        try:
            state = json.loads((self.root / STATE_FILE).read_text(encoding='utf-8'))
            if not isinstance(state, dict):
                raise TypeError('refresh state must be an object')
            self.checked_day = state.get('checked_day')
            self.retry_after = datetime.fromisoformat(state['retry_after']) if state.get('retry_after') else None
            if self.retry_after is not None and self.retry_after.utcoffset() is None:
                raise ValueError('naive retry timestamp')
        except (OSError, ValueError, KeyError, TypeError):
            self.checked_day = None
            self.retry_after = None

    def is_due(self, now: datetime) -> bool:
        day = refresh_day(now)
        if self.retry_after is not None:
            return now >= self.retry_after
        return self.checked_day != day

    def _save_state(self, *, digest: str | None = None) -> None:
        value = {'checked_day': self.checked_day,
                 'retry_after': self.retry_after.isoformat() if self.retry_after else None,
                 'view_digest': digest}
        _write_atomic(self.root / STATE_FILE, json.dumps(value, ensure_ascii=False, indent=2).encode('utf-8'))

    def _previous(self):
        for root in (self.root, self.previous_root):
            if root is None:
                continue
            try:
                payload, image = read_current_schedule(root)
                if not isinstance(payload.get('render'), dict) or not payload.get('source_version'):
                    continue
                return payload, image, root
            except (OSError, ValueError, KeyError, TypeError):
                continue
        return None

    def refresh(self, now: datetime, *, force: bool = False) -> dict:
        day = refresh_day(now)
        if not force and not self.is_due(now):
            return {'status': 'deferred'}
        calendar_file = self.root / 'calendar.json'
        importing = not calendar_file.is_file()
        if importing and not (self.root / 'bootstrap-input.json').is_file():
            # No inputs yet: defer cheaply without making a daily game job or files.
            self.retry_after = now + timedelta(minutes=5)
            return {'status': 'deferred'}
        try:
            calendar = (load_bootstrap_calendar(self.root, self.bootstrap_config) if importing
                        else json.loads(calendar_file.read_text(encoding='utf-8')))
            payload = project_calendar(calendar, now=now, forecast_limit=self.forecast_limit)
            if importing:
                if not payload['events']:
                    raise ValueError('bootstrap input contains no valid character pools')
                _write_atomic(calendar_file, json.dumps(calendar, ensure_ascii=False, indent=2).encode('utf-8'))
            digest = view_digest(payload['render'])
            previous = self._previous()
            same_view = previous is not None and view_digest(previous[0]['render']) == digest
            if same_view and previous[0]['source_version'] == payload['source_version'] and previous[2] == self.root:
                status = 'no_change'
                published = previous[0]
            else:
                png = previous[1].read_bytes() if same_view else render_schedule_png(payload, node_bin=self.node_bin)
                published = publish_schedule_snapshot(self.root, {**payload, 'view_digest': digest}, png)
                status = 'reused' if same_view else 'rendered'
            self.checked_day = day
            self.retry_after = None
            self._save_state(digest=digest)
            LOGGER.info('stage=rerun_refresh status=%s source_version=%s day=%s',
                        status, payload['source_version'], day)
            return {'status': status, 'payload': published}
        except Exception as error:
            self.retry_after = now + timedelta(minutes=5)
            try:
                self._save_state()
            except OSError:
                LOGGER.exception('stage=rerun_refresh status=failed reason=state_write')
            LOGGER.exception('stage=rerun_refresh status=failed retry_after=%s', self.retry_after.isoformat())
            return {'status': 'failed', 'error': str(error)[:500]}
