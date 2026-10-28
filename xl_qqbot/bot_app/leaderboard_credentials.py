"""Credential access for the opt-in leaderboard archive job.

One game account may back both the server-status probe and the leaderboard
archive job. Renewal of the same private state has exactly one owner: when
the status monitor already renews a state path, the archive job reads that
state read-only; only an independent path may be renewed by the job itself.
Every account serializes logins (renewal tick and gateway session login)
through one shared asyncio lock keyed by the resolved state path. Secrets
never reach logs: only fixed codes are logged.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import time
import tomllib
from pathlib import Path

from gacha_core.gacha import GachaError, parse_credentials
from leaderboard_cli import LEADERBOARD_APP_IDS
from starfall_refresh.core import Engine, SafeError, Store
from starfall_refresh.http import HTTP

from .config import LeaderboardConfig

_logger = logging.getLogger(__name__)

_ACCOUNT_LOCKS: dict[str, asyncio.Lock] = {}
_RENEWAL_OWNERS: dict[str, str] = {}


class CredentialError(Exception):
    """Fixed-code credential failure; never carries secret-bearing text."""

    def __init__(self, code: str):
        self.code = code
        super().__init__(code)


def _resolved(path: str | Path) -> str:
    return os.path.normcase(str(Path(path).absolute()))


def same_credentials_path(first: str | Path, second: str | Path) -> bool:
    return _resolved(first) == _resolved(second)


def shared_account_lock(path: str | Path) -> asyncio.Lock:
    """One in-process lock per credentials path, shared with the status probe."""
    key = _resolved(path)
    lock = _ACCOUNT_LOCKS.get(key)
    if lock is None:
        lock = asyncio.Lock()
        _ACCOUNT_LOCKS[key] = lock
    return lock


def claim_renewal_owner(path: str | Path, owner: str) -> bool:
    """A state path has at most one renewal owner; the first claim wins."""
    key = _resolved(path)
    current = _RENEWAL_OWNERS.get(key)
    if current is not None and current != owner:
        return False
    _RENEWAL_OWNERS[key] = owner
    return True


def release_renewal_owner(path: str | Path, owner: str) -> None:
    key = _resolved(path)
    if _RENEWAL_OWNERS.get(key) == owner:
        del _RENEWAL_OWNERS[key]


def renewal_owner(path: str | Path) -> str | None:
    return _RENEWAL_OWNERS.get(_resolved(path))


def _fixed_failure(code: str) -> CredentialError:
    _logger.warning("leaderboard credentials unavailable: %s", code)
    return CredentialError(code)


def _parse_token(token: str, *, open_id=None, channel=None):
    try:
        web = parse_credentials(token, supported_app_ids=LEADERBOARD_APP_IDS)
    except GachaError as error:
        raise _fixed_failure(error.code) from None
    if open_id is not None and web.open_id != str(open_id):
        raise _fixed_failure("INVALID_CREDENTIAL_BINDING")
    if channel is not None and web.channel != str(channel):
        raise _fixed_failure("INVALID_CREDENTIAL_BINDING")
    return web


class LeaderboardCredentialProvider:
    """Serialize credential reads (and opt-in renewal) for one account."""

    def __init__(
        self,
        config: LeaderboardConfig,
        *,
        read_only: bool = False,
        account_lock: asyncio.Lock | None = None,
        store_factory=Store,
        engine_factory=Engine,
        http_factory=HTTP,
        now=time.time,
    ):
        self._config = config
        self._renewal = bool(config.refresh_enabled)
        self._read_only = read_only
        self._lock = account_lock or shared_account_lock(config.credentials_file)
        self._store_factory = store_factory
        self._engine_factory = engine_factory
        self._http_factory = http_factory
        self._now = now
        self._engine = None
        self._store = None
        if self._renewal:
            self._store = store_factory(config.credentials_file)
            self.startup_check()

    def bind_account_lock(self, lock: asyncio.Lock) -> None:
        """Attach the lock shared with the status probe for this account."""
        self._lock = lock

    def startup_check(self) -> None:
        """Fail before any request on an invalid or unsafe private state."""
        try:
            with self._store.locked():
                self._store.load()
        except SafeError as error:
            raise _fixed_failure(error.code) from None

    def _renewal_tick(self) -> None:
        if self._engine is None:
            http = self._http_factory(self._config.refresh_proxy)
            self._engine = self._engine_factory(
                self._store, http, now=self._now, track_events=False
            )
        result = self._engine.tick(gateway=False)
        health = result.get("refresh")
        if health in ("AUTH_ACTION_REQUIRED",):
            _logger.warning("leaderboard credential renewal state=%s", health)
        else:
            _logger.info("leaderboard credential renewal state=%s", health)

    def _read_state_token(self) -> str:
        try:
            with self._store.locked():
                data = self._store.load()
        except SafeError as error:
            raise _fixed_failure(error.code) from None
        credential = data.get("credential", {})
        token = credential.get("token")
        if not isinstance(token, str) or not token.strip():
            raise _fixed_failure("STATE_READ_FAILED")
        return token

    def _read_legacy_token(self) -> tuple[str, str | None, str | None]:
        path = Path(self._config.credentials_file)
        try:
            content = path.read_text(encoding="utf-8")
        except OSError:
            raise _fixed_failure("CREDENTIALS_FILE_UNAVAILABLE") from None
        if os.name == "posix" and path.stat().st_mode & 0o077:
            raise _fixed_failure("PRIVATE_PERMISSIONS_REQUIRED")
        raw = content.strip()
        if raw.startswith("{"):
            try:
                payload = json.loads(raw)
            except ValueError:
                raise _fixed_failure("CREDENTIALS_FILE_INVALID") from None
        elif raw.startswith("["):
            raise _fixed_failure("CREDENTIALS_FILE_INVALID")
        else:
            try:
                payload = tomllib.loads(raw)
            except tomllib.TOMLDecodeError:
                # Bare JWT credential line.
                return raw, None, None
        if not isinstance(payload, dict):
            raise _fixed_failure("CREDENTIALS_FILE_INVALID")
        token = payload.get("token")
        if not isinstance(token, str) or not token.strip():
            raise _fixed_failure("CREDENTIALS_FILE_INVALID")
        open_id = payload.get("open_id")
        channel = payload.get("channel")
        return (
            token.strip(),
            str(open_id).strip() if open_id is not None else None,
            str(channel).strip() if channel is not None else None,
        )

    async def credentials(self):
        """Return WebCredentials, renewing first when this provider owns renewal."""
        async with self._lock:
            if self._renewal:
                if not self._read_only:
                    await asyncio.to_thread(self._renewal_tick)
                token = await asyncio.to_thread(self._read_state_token)
                return await asyncio.to_thread(_parse_token, token)
            token, open_id, channel = await asyncio.to_thread(self._read_legacy_token)
            return await asyncio.to_thread(
                _parse_token, token, open_id=open_id, channel=channel
            )
