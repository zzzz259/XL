"""Instance-local connection from the QQ worker to the guild jobs process."""

from __future__ import annotations

from dataclasses import asdict
from pathlib import Path

import aiohttp

from .guild_roster import GuildEntry, GuildReply


def guild_directory(data_dir: str) -> Path:
    return Path(data_dir).resolve() / "guild-roster"


def guild_query_valid(value: object) -> bool:
    return (
        isinstance(value, str)
        and bool(value.strip(" .．。\t\r\n\u3000"))
        and len(value.encode("utf-8", errors="replace")) <= 240
        and all(ord(character) >= 32 and character != "\x7f" for character in value)
    )


class GuildClient:
    def __init__(self, directory: Path):
        self._socket = str(directory.resolve() / "jobs.sock")

    async def request(self, action: str, value: str | GuildEntry | None = None) -> GuildReply:
        data = {"action": action, "value": asdict(value) if isinstance(value, GuildEntry) else value}
        try:
            async with (
                aiohttp.ClientSession(
                    connector=aiohttp.UnixConnector(path=self._socket),
                    timeout=aiohttp.ClientTimeout(total=240),
                ) as session,
                session.post("http://guild-roster/action", json=data) as response,
            ):
                response.raise_for_status()
                payload = await response.json()
            return GuildReply(
                payload["status"], payload["name"],
                tuple(GuildEntry(**row) for row in payload["options"]),
                payload["failed"],
            )
        except (aiohttp.ClientError, TimeoutError, OSError, ValueError, KeyError, TypeError):
            return GuildReply("unavailable")
