"""One-time import of audited parsed fields for an already-published game version."""
from __future__ import annotations

import json
from datetime import date, datetime
from pathlib import Path

from .config import DEFAULT_BOOTSTRAP_CYCLES
from .rerun_calendar import (
    DEFAULT_CYCLE_ANCHOR,
    SHANGHAI,
    cycle_for_date,
    merge_calendar,
    pool_window,
)
from .versioning import read_current_pointer


def version_observed_at(data_dir: Path, version: str, fallback: datetime | None = None) -> datetime | None:
    events = data_dir / 'update_events.jsonl'
    if events.is_file():
        with events.open(encoding='utf-8') as handle:
            for line in handle:
                try:
                    item = json.loads(line)
                    if not isinstance(item, dict) or item.get('event') != 'start' or str(item.get('version')) != version:
                        continue
                    observed = datetime.fromisoformat(item['at'])
                    if observed.tzinfo is not None and observed.utcoffset() is not None:
                        return observed.astimezone(SHANGHAI)
                except (ValueError, KeyError, TypeError):
                    continue
    return fallback


def bootstrap_rerun_calendar(pools: list[dict], config=None) -> dict:
    """Seed evidenced historical dates once; never use installation day for old pools."""
    value = {'schema_version': 1,
             'cycle_anchor_date': getattr(config, 'rerun_schedule_cycle_anchor_date', DEFAULT_CYCLE_ANCHOR.isoformat()),
             'archived_pool_ids': [], 'pools': {}, 'versions': {}, 'diagnostics': []}
    cutoff = getattr(config, 'rerun_schedule_history_through_gacha_id', 24000087)
    cycles = getattr(config, 'rerun_schedule_bootstrap_cycles', DEFAULT_BOOTSTRAP_CYCLES)
    by_id = {int(identifier): cycle for cycle in cycles for identifier in cycle['gacha_ids']}
    for raw in pools:
        try:
            identifier = int(raw['id'])
            activity_pool = int(raw.get('type', 0)) == 2
        except (KeyError, ValueError, TypeError):
            continue  # merge_calendar records invalid individual input
        if identifier <= 0 or isinstance(raw['id'], bool) or not activity_pool:
            continue
        pool = dict(raw)
        seed = by_id.get(identifier)
        if seed:
            start, end = pool_window(date.fromisoformat(seed['cycle_start']))
            pool.update(cycle_start=seed['cycle_start'], start_at=start.isoformat(), end_at=end.isoformat(),
                        date_source='version_cycle', source_reference=seed['source'],
                        first_seen_version=str(seed.get('source_version', 'historical:' + seed['cycle_start'])))
        elif identifier <= cutoff:
            value['archived_pool_ids'].append(str(identifier))
            pool.update(first_seen_version='archived', source_reference='previous_validated_history')
        else:
            continue
        pool['gacha_id'] = identifier
        value['pools'][str(identifier)] = pool
    return value


def load_bootstrap_calendar(schedule_dir: Path, config=None) -> dict:
    """Called under the updater lock; the input must match the active version."""
    data_dir = schedule_dir.parent
    raw = json.loads((schedule_dir / 'bootstrap-input.json').read_text(encoding='utf-8'))
    if not isinstance(raw, dict):
        raise TypeError('bootstrap input must be an object')
    version = str(raw.get('source_version', '')).strip()
    current = read_current_pointer(data_dir)
    if current is None or version != str(current):
        raise ValueError('bootstrap input source_version must match current game version')
    pools, bottomups, names = raw.get('pools'), raw.get('bottomups'), raw.get('names')
    if not isinstance(pools, list) or not pools or not all(isinstance(pool, dict) for pool in pools):
        raise TypeError('bootstrap pools must be a non-empty object array')
    if not isinstance(bottomups, dict) or not isinstance(names, dict):
        raise TypeError('bootstrap bottomups and names must be objects')
    previous = bootstrap_rerun_calendar(pools, config)
    previous['overrides'] = list(getattr(config, 'rerun_schedule_overrides', ()))
    observed = version_observed_at(data_dir, version)
    if observed is not None:
        anchor = date.fromisoformat(previous['cycle_anchor_date'])
        previous['versions'][version] = {'first_seen_at': observed.isoformat(),
                                        'cycle_start': cycle_for_date(observed.date(), anchor)[0].isoformat()}
    # With no actual event, historical_import skips unknown pools. The fallback
    # clock only satisfies timezone validation, never supplies their cycle.
    return merge_calendar(previous, pools, bottomups, names, source_version=version,
                          observed_at=observed or datetime.now(SHANGHAI), historical_import=True)
