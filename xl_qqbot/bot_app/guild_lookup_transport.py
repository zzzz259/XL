"""Read-only public guild lookups over a private Unix socket or loopback HTTP."""

from __future__ import annotations

import asyncio
import hmac
import re
from collections.abc import Awaitable, Callable
from pathlib import Path

import aiohttp
from aiohttp import web

from .guild_ipc import guild_query_valid
from .guild_roster import GuildAccountBusy, GuildEntry, GuildLookup

GUILD_LOOKUP_PATH = "/internal/guild-lookup"
_AUTH_CONTEXT = b"xl/qqbot/guild-lookup/v1"
_UNIX_ORIGIN = "http://guild-roster"
_LOOPBACK_ORIGIN = re.compile(r"http://127\.0\.0\.1:([0-9]{1,5})")
_MAX_NAMES = 48
_MAX_REQUEST_BYTES = 40 * 1024
_LOOKUP_TIMEOUT = 230  # Leave time for GuildClient's 240-second action request.
_BUSY = {"code": "guild_account_busy"}


def _authorization(control_token: str) -> str:
    return "Bearer " + hmac.new(control_token.encode(), _AUTH_CONTEXT, "sha256").hexdigest()


def _valid_name(name: object) -> bool:
    if not isinstance(name, str) or not guild_query_valid(name):
        return False
    try:
        name.encode("utf-8")
    except UnicodeError:
        return False
    return True


def _public_entry(row: GuildEntry) -> dict[str, str | int | float | None]:
    return {
        "guild_uid": row.guild_uid,
        "name": row.name,
        "member_count": row.member_count,
        "capacity": row.capacity,
        "last_rank": row.last_rank,
        "captured_at": row.captured_at,
    }


def guild_lookup_handler(
    lookup: Callable[[tuple[str, ...]], Awaitable[dict[str, GuildLookup]]],
    *,
    control_token: str | None = None,
) -> Callable[[web.Request], Awaitable[web.Response]]:
    """Build the lookup handler. Pass no token on the private Unix socket."""
    if control_token == "":
        raise ValueError("The guild lookup control token cannot be empty")
    expected = None if control_token is None else _authorization(control_token).encode()

    async def handle(request: web.Request) -> web.Response:
        if expected is not None:
            authorization = request.headers.getall("Authorization", ())
            if len(authorization) != 1 or not hmac.compare_digest(
                authorization[0].encode("utf-8", errors="replace"), expected
            ):
                raise web.HTTPUnauthorized()

        if request.content_type != "application/json":
            raise web.HTTPBadRequest()
        try:
            payload = await request.clone(client_max_size=_MAX_REQUEST_BYTES).json()
        except (ValueError, LookupError, RecursionError):
            raise web.HTTPBadRequest() from None
        if not isinstance(payload, dict) or payload.keys() != {"names"}:
            raise web.HTTPBadRequest()
        names = payload["names"]
        if not isinstance(names, list) or not 1 <= len(names) <= _MAX_NAMES:
            raise web.HTTPBadRequest()
        if not all(_valid_name(name) for name in names):
            raise web.HTTPBadRequest()

        queries = tuple(dict.fromkeys(names))
        try:
            results = await lookup(queries)
            return web.json_response({"results": {
                name: {
                    "guilds": [_public_entry(row) for row in results[name].guilds],
                    "has_more": results[name].has_more,
                }
                for name in queries if name in results
            }})
        except GuildAccountBusy:
            return web.json_response(_BUSY, status=503)
        except Exception:  # noqa: BLE001 - keep credential-path exceptions private
            return web.json_response({"code": "guild_lookup_unavailable"}, status=502)

    return handle


def _decode_lookup(payload: dict) -> GuildLookup:
    guilds, has_more = payload["guilds"], payload["has_more"]
    if not isinstance(guilds, list) or not isinstance(has_more, bool):
        raise TypeError("Invalid guild lookup response")
    return GuildLookup(tuple(GuildEntry(**row) for row in guilds), has_more)


class GuildLookupClient:
    def __init__(
        self, origin: str, *, control_token: str | None = None, socket_path: Path | None = None
    ):
        self._socket = socket_path
        self._headers = {}
        if socket_path is None:
            match = _LOOPBACK_ORIGIN.fullmatch(origin)
            if match is None or not 1 <= int(match[1]) <= 65535:
                raise ValueError("Guild lookup HTTP origin must be http://127.0.0.1:<port>")
            if not control_token:
                raise ValueError("Guild lookup over HTTP requires a control token")
            self._headers["Authorization"] = _authorization(control_token)
        elif origin != _UNIX_ORIGIN or control_token is not None:
            raise ValueError("Guild lookup over Unix requires http://guild-roster without a token")
        self._url = origin + GUILD_LOOKUP_PATH

    async def __call__(self, names: tuple[str, ...]) -> dict[str, GuildLookup]:
        queries = tuple(dict.fromkeys(name for name in names if _valid_name(name)))
        if not queries:
            return {}
        results: dict[str, GuildLookup] = {}
        try:
            connector = None if self._socket is None else aiohttp.UnixConnector(path=str(self._socket))
            async with (
                asyncio.timeout(_LOOKUP_TIMEOUT),
                aiohttp.ClientSession(
                    connector=connector, headers=self._headers, trust_env=False,
                    timeout=aiohttp.ClientTimeout(total=_LOOKUP_TIMEOUT, connect=5),
                ) as session,
            ):
                for index in range(0, len(queries), _MAX_NAMES):
                    batch = queries[index:index + _MAX_NAMES]
                    async with session.post(self._url, json={"names": batch}, allow_redirects=False) as response:
                        if response.status == 503 and await response.json() == _BUSY:
                            if results:
                                break
                            raise GuildAccountBusy
                        if response.status != 200:
                            break
                        payload = (await response.json())["results"]
                        if not isinstance(payload, dict):
                            break
                        results.update({name: _decode_lookup(payload[name]) for name in batch if name in payload})
        except (aiohttp.ClientError, OSError, ValueError, KeyError, TypeError, RecursionError):
            return results
        return results
