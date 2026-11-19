"""Read-only guild collection in the instance's existing automation process."""

from __future__ import annotations

import asyncio
import logging
import sqlite3
import time
from dataclasses import asdict
from datetime import datetime, timedelta, timezone

from aiohttp import web
from gacha_core.gacha import GachaError
from gacha_core.guild_search import search_guilds
from gacha_core.session import GameGatewaySession

from .config import Config
from .guild_ipc import guild_directory, guild_query_valid
from .guild_poster import render_guild_poster
from .guild_roster import (
    GuildAccountBusy,
    GuildEntry,
    GuildLookup,
    GuildRoster,
    GuildRosterStore,
)
from .leaderboard_credentials import (
    CredentialError,
    LeaderboardCredentialProvider,
    shared_game_session_lock,
)

_logger = logging.getLogger(__name__)
_BJT = timezone(timedelta(hours=8))
_SESSION_SECONDS = 180
_LOGIN_SECONDS = 25


def guild_slots(now: float, times: tuple[str, str]) -> tuple[float, float]:
    local = datetime.fromtimestamp(now, _BJT)
    today = tuple(
        local.replace(hour=int(value[:2]), minute=int(value[3:]), second=0, microsecond=0)
        for value in sorted(times)
    )
    dates = (today[1] - timedelta(days=1), *today, today[0] + timedelta(days=1))
    slots = tuple(value.timestamp() for value in dates)
    return max(slot for slot in slots if slot <= now), min(slot for slot in slots if slot > now)


class GuildCollector:
    def __init__(self, provider, account_lock, request_delay, *, session_factory=GameGatewaySession, now=time.time):
        self._provider = provider
        self._account_lock = account_lock
        self._request_delay = request_delay
        self._session_factory = session_factory
        self._now = now

    async def __call__(self, names: tuple[str, ...]) -> dict[str, GuildLookup]:
        try:
            await asyncio.wait_for(self._account_lock.acquire(), timeout=45)
        except TimeoutError:
            _logger.info("guild collection waiting on another game session timed out")
            raise GuildAccountBusy from None
        try:
            credentials = await self._provider.credentials()
            task = asyncio.create_task(asyncio.to_thread(self._read, credentials, names))
            try:
                return await asyncio.shield(task)
            except asyncio.CancelledError:
                await asyncio.gather(task, return_exceptions=True)
                raise
        except CredentialError as error:
            _logger.warning("guild credentials unavailable: %s", error.code)
            return {}
        finally:
            self._account_lock.release()

    def _read(self, credentials, names: tuple[str, ...]) -> dict[str, GuildLookup]:
        results = {}
        batch_size = int((_SESSION_SECONDS - _LOGIN_SECONDS) // max(1.0, self._request_delay))
        try:
            for index in range(0, len(names), batch_size):
                batch = names[index:index + batch_size]
                with self._session_factory(
                    credentials,
                    max_requests=len(batch),
                    max_duration=_LOGIN_SECONDS + len(batch) * self._request_delay,
                    request_delay=self._request_delay,
                ) as session:
                    for name in batch:
                        try:
                            page = search_guilds(session, name)
                        except GachaError as error:
                            if error.code != "GUILD_SEARCH_PROTOCOL_ERROR":
                                raise
                            _logger.warning("guild search page could not be decoded")
                            continue
                        observed = self._now()
                        results[name] = GuildLookup(tuple(
                            GuildEntry(guild.guild_uid, guild.name, guild.member_count,
                                       guild.capacity, guild.last_rank, observed)
                            for guild in page.guilds
                        ), page.has_more)
        except GachaError as error:
            _logger.warning("guild collection incomplete: %s", error.code)
        return results


class GuildJobs:
    def __init__(self, roster: GuildRoster, times: tuple[str, str], *, now=time.time):
        self._roster = roster
        self._times = times
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
    lb = config.leaderboard
    provider = LeaderboardCredentialProvider(lb, read_only=True)
    collector = GuildCollector(provider, shared_game_session_lock(lb.credentials_file), lb.request_delay)
    store = GuildRosterStore(guild_directory(config.watch.data_dir))
    return GuildJobs(GuildRoster(store, collector, render_guild_poster), config.guild_roster.refresh_times)
