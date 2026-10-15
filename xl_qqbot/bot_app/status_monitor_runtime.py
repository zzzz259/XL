"""Router-side assembly for the optional game server status monitor."""

from __future__ import annotations

import logging
from collections.abc import Callable, Iterable
from pathlib import Path

from .config import Config
from .proactive_outbox import ProactiveOutbox
from .status_monitor import Probe, StatusHistory, StatusMonitor

_logger = logging.getLogger(__name__)


def select_status_recipients(
    debug_groups: Iterable[str], test_groups: Iterable[str], recipient_tiers: Iterable[str]
) -> tuple[str, ...]:
    """Select unique groups by tier, preserving GroupTier's debug-first rule."""
    debug_groups = tuple(debug_groups)
    test_groups = tuple(test_groups)
    allowed = frozenset(recipient_tiers) & {"debug", "test"}
    selected = []
    seen = set()
    debug_set = frozenset(debug_groups)
    if "debug" in allowed:
        for group in debug_groups:
            if group and group not in seen:
                selected.append(group)
                seen.add(group)
    if "test" in allowed:
        for group in test_groups:
            if group and group not in seen and group not in debug_set:
                selected.append(group)
                seen.add(group)
    return tuple(selected)


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
        _logger.warning("服务器状态监控已配置开启，但未注入凭据/探针 provider；保持停用")
        return None

    recipients = select_status_recipients(
        config.groups.debug,
        config.groups.test,
        status_config.recipient_tiers,
    )
    if not recipients:
        _logger.warning("服务器状态监控没有符合 tier 的已配置群；保持停用")
        return None

    return StatusMonitor(
        probe=probe_factory(config),
        history=StatusHistory(Path(config.watch.data_dir) / "server_status.sqlite3"),
        outbox=outbox,
        recipients=recipients,
        interval_seconds=status_config.interval_seconds,
        confirm_delay_seconds=status_config.confirm_delay_seconds,
        notification_client=notification_client,
    )
