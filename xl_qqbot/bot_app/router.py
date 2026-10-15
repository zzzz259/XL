"""Compatibility entry point for the single QQ Gateway process."""

from __future__ import annotations

import asyncio
import logging
import signal

from .config import Config, load_config
from .gateway import (
    GatewayRuntime,
    RouterClient as _RouterClient,
    TierForwarder,
    install_raw_parsers as _install_raw_parsers,
    run_gateway_client,
    worker_ports,
)

_logger = logging.getLogger(__name__)


def _setup_logging() -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    )
    # botpy may have installed a WARNING root handler before basicConfig runs.
    logging.getLogger().setLevel(logging.INFO)


async def _amain_router(config: Config) -> None:
    """Run the Gateway runtime; retained for callers using the old entry point."""
    runtime = GatewayRuntime(config)
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGTERM, signal.SIGINT):
        try:
            loop.add_signal_handler(sig, runtime.stop)
        except (NotImplementedError, RuntimeError):
            # Signal handlers are unavailable on some event loops/platforms.
            _logger.debug("Signal handler unavailable for %s", sig)
    await runtime.run()


def main() -> None:
    _setup_logging()
    # Register raw event parsing before botpy constructs its connection state.
    _install_raw_parsers()
    asyncio.run(_amain_router(load_config("config.toml")))


if __name__ == "__main__":
    main()
