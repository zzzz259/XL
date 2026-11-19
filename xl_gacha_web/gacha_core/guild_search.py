"""Read one page of public guild aggregates by name."""

from __future__ import annotations

from dataclasses import dataclass

from .gacha import GachaError, blob, integer
from .leaderboards import _decode, _ProtocolError, _scalar, _schema
from .session import GameGatewaySession

_SEARCH_GUILD_COMMAND = 10032  # SearchGuildByConditionsReq
_GUILD_INFO = _schema(
    _scalar(1, 'guild_uid', 'i64'),
    _scalar(2, 'name', 'str'),
    _scalar(5, 'level'),
    _scalar(11, 'member_count'),
    _scalar(26, 'last_rank'),
)
_PAGE = _schema((1, 'guilds', _GUILD_INFO, True), _scalar(2, 'has_more', 'bool'))

# BaseGuildLevelUp.lua lists these capacities for guild levels 1–10.
_CAPACITY_BY_LEVEL = dict(enumerate((20, 21, 22, 23, 24, 25, 26, 27, 28, 30), start=1))


@dataclass(frozen=True)
class PublicGuild:
    guild_uid: str
    name: str
    level: int
    member_count: int
    last_rank: int | None

    @property
    def capacity(self) -> int | None:
        return _CAPACITY_BY_LEVEL.get(self.level)


@dataclass(frozen=True)
class GuildSearchResult:
    guilds: tuple[PublicGuild, ...]
    has_more: bool


def _public_guild(info: dict) -> PublicGuild:
    guild_uid = info.get('guild_uid', 0)
    name = info.get('name', '')
    level = info.get('level', 0)
    member_count = info.get('member_count', 0)
    last_rank = info.get('last_rank', 0)
    if guild_uid <= 0 or not name.strip() or level <= 0 or member_count < 0:
        raise _ProtocolError()
    return PublicGuild(str(guild_uid), name, level, member_count,
                       last_rank if last_rank > 0 else None)


def search_guilds(session: GameGatewaySession, name: str, *, offset: int = 0) -> GuildSearchResult:
    """Send one name search through the supplied bounded gateway session."""
    name = name.strip() if isinstance(name, str) else ''
    if not name:
        raise GachaError('INVALID_GUILD_SEARCH_NAME')
    if type(offset) is not int or not 0 <= offset < 2**31:
        raise GachaError('INVALID_GUILD_SEARCH_OFFSET')
    try:
        encoded_name = name.encode('utf-8')
    except UnicodeError:
        raise GachaError('INVALID_GUILD_SEARCH_NAME') from None

    filters = b''.join(integer(field, 0) for field in (3, 4, 5, 7, 8))
    body = integer(1, offset) + blob(2, encoded_name) + filters
    response = session.request(_SEARCH_GUILD_COMMAND, body)
    try:
        page = _decode(response, _PAGE)
        guilds = tuple(_public_guild(info) for info in page.get('guilds', ()))
    except _ProtocolError:
        raise GachaError('GUILD_SEARCH_PROTOCOL_ERROR') from None
    return GuildSearchResult(guilds, page.get('has_more', False))
