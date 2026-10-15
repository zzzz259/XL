"""Tier-specific background jobs for xl_qqbot automation processes."""

from __future__ import annotations

import argparse
import asyncio
import logging
import signal
from dataclasses import dataclass

from .bilibili import BilibiliWatcher
from .config import Config, UpdateSourceConfig, load_config
from .notification_service import NotificationClient
from .status_monitor import Probe
from .status_monitor_runtime import build_status_monitor
from .watcher import Watcher

_logger = logging.getLogger(__name__)


@dataclass
class AutomationRuntime:
    tier: str
    watchers: tuple[Watcher, ...]
    status_monitor: object | None
    bilibili_watcher: BilibiliWatcher | None
    stop_event: asyncio.Event

    async def run(self) -> None:
        jobs = [watcher.run() for watcher in self.watchers]
        if self.status_monitor is not None:
            jobs.append(self.status_monitor.run(self.stop_event))
        if self.bilibili_watcher is not None:
            jobs.append(self.bilibili_watcher.run())
        if not jobs:
            await self.stop_event.wait()
            return
        await asyncio.gather(*jobs)

    def stop(self) -> None:
        self.stop_event.set()
        for watcher in self.watchers:
            watcher.stop()
        if self.bilibili_watcher is not None:
            self.bilibili_watcher.stop()


def build_automation_runtime(
    config: Config,
    *,
    tier: str,
    notification_client: NotificationClient,
    probe_factory=None,
) -> AutomationRuntime:
    if tier not in ("test", "production"):
        raise ValueError("automation tier must be test or production")
    if tier == "production" and config.server_status.enabled:
        raise ValueError("production automation cannot enable server_status")

    configured_sources = config.watch.update_sources or (
        UpdateSourceConfig(name="main", outbox_dir=config.watch.outbox_dir),
    )
    if tier == "test":
        sources = tuple(source for source in configured_sources if source.name != "main")
    else:
        sources = tuple(source for source in configured_sources if source.name == "main")
        if not sources:
            sources = (
                UpdateSourceConfig(name="main", outbox_dir=config.watch.outbox_dir),
            )

    watchers = tuple(
        Watcher(
            config,
            sender=None,
            source=source,
            notification_client=notification_client,
        )
        for source in sources
    )
    status_monitor = (
        build_status_monitor(
            config,
            outbox=None,
            probe_factory=probe_factory,
            notification_client=notification_client,
        )
        if tier == "test"
        else None
    )
    bilibili_watcher = (
        BilibiliWatcher(config, sender=None, notification_client=notification_client)
        if tier == "production" and config.bilibili.enabled
        else None
    )
    return AutomationRuntime(
        tier=tier,
        watchers=watchers,
        status_monitor=status_monitor,
        bilibili_watcher=bilibili_watcher,
        stop_event=asyncio.Event(),
    )


def parse_args(argv=None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="xl_qqbot tier automation runner")
    parser.add_argument("--tier", required=True, choices=("test", "production"))
    return parser.parse_args(argv)


async def _amain(config: Config, tier: str) -> None:
    port = (
        config.notification.test_port
        if tier == "test"
        else config.notification.production_port
    )
    client = NotificationClient(
        f"http://127.0.0.1:{port}",
        bearer_token=config.notification.api_token,
    )
    await client.start()
    runtime = build_automation_runtime(
        config,
        tier=tier,
        notification_client=client,
        probe_factory=None,
    )
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGTERM, signal.SIGINT):
        loop.add_signal_handler(sig, runtime.stop)
    try:
        await runtime.run()
    finally:
        runtime.stop()
        await client.close()


def main(argv=None) -> None:
    args = parse_args(argv)
    config = load_config("config.toml")
    if len(config.notification.api_token) < 32:
        raise ValueError("[notification] api_token must be configured with 32+ characters")
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    )
    logging.getLogger().setLevel(logging.INFO)
    asyncio.run(_amain(config, args.tier))


if __name__ == "__main__":
    main()
