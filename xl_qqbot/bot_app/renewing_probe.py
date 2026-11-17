"""Opt-in renewal in instance jobs; existing StatusMonitor owns all notifications."""

from __future__ import annotations

import asyncio
import logging
import time
from dataclasses import replace

from starfall_refresh.core import LOGIN_URL, Engine, Store
from starfall_refresh.http import HTTP

from .config import ServerStatusConfig
from .status_monitor import StatusSample
from .status_probe import build_login_preflight_probe

_logger = logging.getLogger(__name__)


class _ProbeHTTP:
    """Retain only the allowlisted login evidence needed by the existing classifier."""

    def __init__(self, client):
        self.client = client
        self.sample = None

    def __call__(self, url, body, headers):
        response = self.client(url, body, headers)
        if url == LOGIN_URL:
            ret = response.get("retCode") if isinstance(response, dict) else None
            data = response.get("data") if isinstance(response, dict) else None
            valid = type(ret) is int
            accepted = valid and ret == 0 and isinstance(data, dict)
            present = isinstance(data, dict) and "maintainState" in data
            value = data.get("maintainState") if present else None
            # Invalid field types must remain UNKNOWN without retaining arbitrary response text.
            value = value if type(value) is int else None
            self.sample = StatusSample(
                accepted,
                ret if valid else None,
                present,
                value,
                False,
                None
                if accepted
                else "AUTH_ERROR"
                if valid and ret != 0
                else "PARSE_ERROR",
            )
        return response

    def gateway(self, addresses):
        result = self.client.gateway(addresses)
        if self.sample is not None:
            self.sample = replace(self.sample, gateway_reachable=result == "REACHABLE")
        return result


def build_status_probe(config: ServerStatusConfig, *, now=time.time):
    """Select the legacy reader or the renewing provider using instance configuration."""
    if not config.refresh_enabled:
        return build_login_preflight_probe(config.credentials_file)
    store = Store(config.credentials_file)
    with store.locked():
        store.load()  # Fail at startup on an invalid/unsafe private state, before any request.
    http = _ProbeHTTP(HTTP(config.refresh_proxy))
    engine = Engine(store, http, now=now, track_events=False)
    last_health = None

    def sample():
        nonlocal last_health
        http.sample = None
        try:
            result = engine.tick(gateway=True)
        except Exception:
            # Never log exceptions: upstream request/storage objects may carry secrets.
            _logger.warning("server status renewal sample unavailable")
            return StatusSample(False, None, False, None, False, "PROBE_ERROR")
        health = result["refresh"]
        if health != last_health:
            _logger.info("server status credential refresh=%s", health)
            last_health = health
        if health == "AUTH_ACTION_REQUIRED" or result["state"] == "AUTH_REQUIRED":
            return StatusSample(False, None, False, None, False, "AUTH_ERROR")
        return http.sample or StatusSample(
            False, None, False, None, False, "NETWORK_ERROR"
        )

    async def probe():
        return await asyncio.to_thread(sample)

    return probe
