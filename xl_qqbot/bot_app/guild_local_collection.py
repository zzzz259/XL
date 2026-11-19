"""Local collection of public guild information by the session owner."""

from __future__ import annotations

import asyncio
import logging
import time

from gacha_core.gacha import GachaError
from gacha_core.guild_search import search_guilds
from gacha_core.session import GameGatewaySession

from .config import Config
from .guild_roster import GuildAccountBusy, GuildEntry, GuildLookup
from .leaderboard_credentials import (
    CredentialError,
    LeaderboardCredentialProvider,
    shared_game_session_lock,
)

_logger = logging.getLogger(__name__)
_SESSION_SECONDS = 180
_LOGIN_SECONDS = 25


class GuildCollector:
    def __init__(self, provider, account_lock, request_delay, *, session_factory=GameGatewaySession, now=time.time):
        self._provider = provider
        self._account_lock = account_lock
        self._request_delay = request_delay
        self._session_factory = session_factory
        self._now = now
        self._in_flight: dict[frozenset[str], asyncio.Task[dict[str, GuildLookup]]] = {}

    async def __call__(self, names: tuple[str, ...]) -> dict[str, GuildLookup]:
        key = frozenset(names)
        task = self._in_flight.get(key)
        if task is None or task.done():
            task = asyncio.create_task(self._collect(names))
            self._in_flight[key] = task

            def discard(completed: asyncio.Task[dict[str, GuildLookup]]) -> None:
                if self._in_flight.get(key) is completed:
                    del self._in_flight[key]

            task.add_done_callback(discard)

        try:
            return (await asyncio.shield(task)).copy()
        except asyncio.CancelledError:
            await asyncio.shield(asyncio.gather(task, return_exceptions=True))
            raise

    async def _collect(self, names: tuple[str, ...]) -> dict[str, GuildLookup]:
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


def build_local_guild_lookup(config: Config) -> GuildCollector:
    leaderboard = config.leaderboard
    provider = LeaderboardCredentialProvider(leaderboard, read_only=True)
    return GuildCollector(
        provider, shared_game_session_lock(leaderboard.credentials_file), leaderboard.request_delay
    )
