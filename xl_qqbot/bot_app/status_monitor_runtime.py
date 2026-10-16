"""Router-side assembly for the optional game server status monitor."""

from __future__ import annotations

import logging
from collections.abc import Callable, Iterable
from pathlib import Path

from .config import Config
from .groups import GroupStore
from .proactive_outbox import ProactiveOutbox
from .status_monitor import Probe, StatusHistory, StatusMonitor

_logger = logging.getLogger(__name__)


def select_status_recipients(groups: Iterable[str], *_legacy) -> tuple[str, ...]:
    """Return unique recipients from this instance's explicit group list."""
    return tuple(dict.fromkeys(group for group in groups if isinstance(group, str) and group))


def build_status_monitor(
    config: Config,
    outbox: ProactiveOutbox | None,
    probe_factory: Callable[[Config], Probe] | None,
    *,
    notification_client=None,
) -> StatusMonitor | None:
    """Build only when explicitly enabled and an external probe is supplied."""
    status_config = config.server_status
    if not status_config.enabled:
        return None
    if probe_factory is None:
        raise RuntimeError(
            "server status monitoring is enabled but no probe provider was injected"
        )

    recipients = select_status_recipients(
        config.target.group_openids
        or (*config.groups.debug, *config.groups.test, *GroupStore(config.watch.data_dir).openids())
    )
    if not recipients:
        _logger.warning("服务器状态监控没有此实例已配置的目标群；保持停用")
        return None

    return StatusMonitor(
        probe=probe_factory(config),
        history=StatusHistory(Path(config.watch.data_dir) / "server_status.sqlite3"),
        outbox=outbox,
        recipients=recipients,
        interval_seconds=status_config.interval_seconds,
        confirm_delay_seconds=status_config.confirm_delay_seconds,
        notification_client=notification_client,
        instance_id=config.instance.instance_id,
    )
