"""Read-only guild collection in the instance's existing automation process."""

from __future__ import annotations

import asyncio
import logging
import sqlite3
import time
from dataclasses import asdict
from datetime import datetime, timedelta, timezone

from aiohttp import web

from .config import Config
from .guild_ipc import guild_directory, guild_query_valid
from .guild_lookup_transport import (
    GUILD_LOOKUP_PATH,
    GuildLookupClient,
    guild_lookup_handler,
)
from .guild_poster import render_guild_poster
from .guild_roster import (
    GuildEntry,
    GuildRoster,
    GuildRosterStore,
)

_logger = logging.getLogger(__name__)
_BJT = timezone(timedelta(hours=8))


def guild_slots(now: float, times: tuple[str, str]) -> tuple[float, float]:
    local = datetime.fromtimestamp(now, _BJT)
    today = tuple(
        local.replace(hour=int(value[:2]), minute=int(value[3:]), second=0, microsecond=0)
        for value in sorted(times)
    )
    dates = (today[1] - timedelta(days=1), *today, today[0] + timedelta(days=1))
    slots = tuple(value.timestamp() for value in dates)
    return max(slot for slot in slots if slot <= now), min(slot for slot in slots if slot > now)


class GuildJobs:
    def __init__(self, roster: GuildRoster, times: tuple[str, str], *, served_lookup=None, now=time.time):
        self._roster = roster
        self._times = times
        self._served_lookup = served_lookup
        self._now = now

    async def refresh_if_due(self) -> float:
        due, upcoming = guild_slots(self._now(), self._times)
        previous = await asyncio.to_thread(self._roster.store.last_scheduled_at)
        if previous is None or previous < due:
            result = await self._roster.refresh(scheduled_at=due)
            _logger.info("guild roster scheduled refresh status=%s failed=%d", result.status, result.failed)
            if result.status == "busy":
                return min(upcoming, self._now() + 60)
        return upcoming

    async def run(self, stop: asyncio.Event) -> None:
        await self._roster.initialize()
        app = web.Application(client_max_size=16_384)
        app.router.add_post("/action", self._handle_action)
        if self._served_lookup is not None:
            app.router.add_post(GUILD_LOOKUP_PATH, guild_lookup_handler(self._served_lookup))
        runner = web.AppRunner(app, shutdown_timeout=60)
        socket_path = self._roster.store.path.with_name("jobs.sock")
        await runner.setup()
        try:
            await web.UnixSite(runner, str(socket_path)).start()
            socket_path.chmod(0o600)
            while not stop.is_set():
                try:
                    upcoming = await self.refresh_if_due()
                    delay = min(3600, max(0, upcoming - self._now()))
                except (OSError, RuntimeError, sqlite3.Error, ValueError):
                    _logger.exception("guild scheduled refresh failed")
                    delay = 60
                try:
                    await asyncio.wait_for(stop.wait(), timeout=delay)
                except TimeoutError:
                    pass
        finally:
            await runner.cleanup()
            await self._roster.finish_refresh()

    async def _handle_action(self, request: web.Request) -> web.Response:
        try:
            data = await request.json()
        except ValueError:
            raise web.HTTPBadRequest() from None
        if not isinstance(data, dict):
            raise web.HTTPBadRequest()
        action, value = data.get("action"), data.get("value")
        if action == "refresh":
            reply = await self._roster.refresh()
        elif action == "add" and guild_query_valid(value):
            reply = await self._roster.add(value)
        elif action == "delete" and guild_query_valid(value):
            reply = await self._roster.delete(value)
        elif action == "delete_selected" and isinstance(value, str) and value.isdecimal():
            reply = await self._roster.delete_selected(value)
        elif action == "add_selected" and isinstance(value, dict):
            try:
                choice = GuildEntry(**value)
            except TypeError:
                raise web.HTTPBadRequest() from None
            if not guild_query_valid(choice.name) or not isinstance(choice.guild_uid, str) or not choice.guild_uid.isdecimal():
                raise web.HTTPBadRequest()
            reply = await self._roster.add_selected(choice)
        else:
            raise web.HTTPBadRequest()
        return web.json_response(asdict(reply))


def build_guild_jobs(config: Config) -> GuildJobs:
    settings = config.guild_roster
    if settings.lookup_origin:
        lookup = GuildLookupClient(settings.lookup_origin, control_token=config.notification.api_token)
    else:
        from .guild_local_collection import build_local_guild_lookup

        lookup = build_local_guild_lookup(config)
    store = GuildRosterStore(guild_directory(config.watch.data_dir))
    return GuildJobs(
        GuildRoster(store, lookup, render_guild_poster),
        settings.refresh_times,
        served_lookup=lookup if settings.serve_lookup else None,
    )
