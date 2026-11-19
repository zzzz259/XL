"""Instance-local recruitment roster, public observations and cached poster."""

from __future__ import annotations

import asyncio
import json
import sqlite3
from contextlib import closing, contextmanager
from dataclasses import asdict, dataclass
from pathlib import Path


@dataclass(frozen=True)
class GuildEntry:
    guild_uid: str
    name: str
    member_count: int | None = None
    capacity: int | None = None
    last_rank: int | None = None
    captured_at: float | None = None

    @property
    def vacancies(self) -> int | None:
        if self.member_count is None or self.capacity is None:
            return None
        return max(0, self.capacity - self.member_count)


@dataclass(frozen=True)
class GuildLookup:
    guilds: tuple[GuildEntry, ...]
    has_more: bool = False


@dataclass(frozen=True)
class GuildReply:
    status: str
    name: str = ""
    options: tuple[GuildEntry, ...] = ()
    failed: int = 0


@dataclass(frozen=True)
class GuildSnapshot:
    rows: tuple[GuildEntry, ...]
    stale_ids: frozenset[str] = frozenset()


class GuildAccountBusy(Exception):
    """The collection could not acquire the shared game session."""


def load_guild_seed() -> tuple[GuildEntry, ...]:
    raw = Path(__file__).with_name("guild_seed.json").read_text(encoding="utf-8")
    return tuple(GuildEntry(**row) for row in json.loads(raw))


class GuildRosterStore:
    """Publish the data and rendered image in the same SQLite transaction."""

    def __init__(self, directory: Path):
        self.path = directory.resolve() / "roster.sqlite3"

    def _read(self, sql: str):
        if not self.path.is_file():
            return None
        with closing(sqlite3.connect(self.path.as_uri() + "?mode=ro", uri=True)) as db:
            return db.execute(sql).fetchone()

    def load(self) -> GuildSnapshot | None:
        result = self._read("SELECT snapshot FROM guild_roster WHERE id = 1")
        if result is None:
            return None
        data = json.loads(result[0])
        return GuildSnapshot(
            tuple(GuildEntry(**row) for row in data["rows"]), frozenset(data["stale"])
        )

    def read_poster(self) -> bytes | None:
        result = self._read("SELECT poster FROM guild_roster WHERE id = 1")
        return result[0] if result is not None else None

    def last_scheduled_at(self) -> float | None:
        result = self._read("SELECT last_scheduled_at FROM guild_roster WHERE id = 1")
        return result[0] if result is not None else None

    def mark_scheduled(self, timestamp: float) -> None:
        with closing(sqlite3.connect(self.path)) as db, db:
            db.execute(
                "UPDATE guild_roster SET last_scheduled_at = ? WHERE id = 1 "
                "AND (last_scheduled_at IS NULL OR last_scheduled_at < ?)",
                (timestamp, timestamp),
            )

    @contextmanager
    def _writer(self):
        self.path.parent.mkdir(parents=True, mode=0o700, exist_ok=True)
        with closing(sqlite3.connect(self.path)) as db, db:
            db.execute("""
                CREATE TABLE IF NOT EXISTS guild_roster (
                    id INTEGER PRIMARY KEY CHECK (id = 1),
                    snapshot TEXT NOT NULL,
                    poster BLOB NOT NULL,
                    last_scheduled_at REAL
                )
                """)
            yield db

    def prepare(self) -> None:
        with self._writer():
            pass

    def save(self, snapshot: GuildSnapshot, poster: bytes) -> None:
        payload = json.dumps(
            {"rows": [asdict(row) for row in snapshot.rows], "stale": sorted(snapshot.stale_ids)},
            ensure_ascii=False,
        )
        with self._writer() as db:
            db.execute("""
                INSERT INTO guild_roster (id, snapshot, poster) VALUES (1, ?, ?)
                ON CONFLICT(id) DO UPDATE SET snapshot = excluded.snapshot, poster = excluded.poster
                """, (payload, poster))


def _without_final_dot(name: str) -> str:
    return name[:-1] if name.endswith((".", "．", "。")) else name


def _resolve(
    query: str, rows: tuple[GuildEntry, ...], *, has_more: bool = False
) -> tuple[GuildEntry | None, tuple[GuildEntry, ...]]:
    rows = tuple({row.guild_uid: row for row in rows}.values())
    exact = tuple(row for row in rows if row.name == query)
    if len(exact) == 1:
        return exact[0], ()
    comparable = _without_final_dot(query)
    dotted = tuple(row for row in rows if _without_final_dot(row.name) == comparable)
    if len(dotted) == 1 and not has_more:
        return dotted[0], ()
    return None, (exact or dotted or rows)[:3]


class GuildRoster:
    """Jobs is the sole writer. A network lookup never holds the roster write lock."""

    def __init__(self, store: GuildRosterStore, lookup, render):
        self.store = store
        self._lookup = lookup
        self._render = render
        self._write_lock = asyncio.Lock()
        self._refresh_task: asyncio.Task | None = None

    async def initialize(self, seed: tuple[GuildEntry, ...] | None = None) -> None:
        async with self._write_lock:
            await asyncio.to_thread(self.store.prepare)
            if await asyncio.to_thread(self.store.load) is None:
                await self._publish(GuildSnapshot(load_guild_seed() if seed is None else seed))

    async def _state(self) -> GuildSnapshot:
        snapshot = await asyncio.to_thread(self.store.load)
        if snapshot is None:
            raise RuntimeError("guild roster is not initialized")
        return snapshot

    async def _publish(self, state: GuildSnapshot) -> None:
        def publish():
            self.store.save(state, self._render(state.rows, state.stale_ids))

        await asyncio.to_thread(publish)

    async def add(self, query: str) -> GuildReply:
        state = await self._state()
        exact = [row for row in state.rows if row.name == query]
        if len(exact) == 1:
            return GuildReply("exists", exact[0].name)
        try:
            lookup = (await self._lookup((query,))).get(query)
        except GuildAccountBusy:
            return GuildReply("busy")
        if lookup is None:
            return GuildReply("unavailable")
        match, options = _resolve(query, lookup.guilds, has_more=lookup.has_more)
        if match is not None:
            return await self.add_selected(match)
        return GuildReply("candidates", options=options) if options else GuildReply("missing")

    async def add_selected(self, row: GuildEntry) -> GuildReply:
        async with self._write_lock:
            state = await self._state()
            existing = next((item for item in state.rows if item.guild_uid == row.guild_uid), None)
            if existing is not None:
                return GuildReply("exists", existing.name)
            await self._publish(GuildSnapshot((*state.rows, row), state.stale_ids))
        return GuildReply("added", row.name)

    async def delete(self, query: str) -> GuildReply:
        state = await self._state()
        normalized = _without_final_dot(query).casefold()
        options = tuple(row for row in state.rows if normalized in row.name.casefold())
        match, options = _resolve(query, options)
        if match is not None:
            return await self.delete_selected(match.guild_uid)
        return GuildReply("candidates", options=options) if options else GuildReply("missing")

    async def delete_selected(self, guild_uid: str) -> GuildReply:
        async with self._write_lock:
            state = await self._state()
            match = next((row for row in state.rows if row.guild_uid == guild_uid), None)
            if match is None:
                return GuildReply("missing")
            rows = tuple(row for row in state.rows if row.guild_uid != guild_uid)
            await self._publish(GuildSnapshot(rows, state.stale_ids - {guild_uid}))
        return GuildReply("deleted", match.name)

    async def refresh(self, *, scheduled_at: float | None = None) -> GuildReply:
        task = self._refresh_task
        if task is None or task.done():
            task = self._refresh_task = asyncio.create_task(self._refresh())
        reply = await asyncio.shield(task)
        if scheduled_at is not None and reply.status != "busy":
            await asyncio.to_thread(self.store.mark_scheduled, scheduled_at)
        return reply

    async def finish_refresh(self) -> None:
        if self._refresh_task is not None:
            await asyncio.gather(self._refresh_task, return_exceptions=True)

    async def _refresh(self) -> GuildReply:
        initial = await self._state()
        if not initial.rows:
            return GuildReply("empty")
        names = tuple(dict.fromkeys(row.name for row in initial.rows))
        try:
            results = await self._lookup(names)
        except GuildAccountBusy:
            return GuildReply("busy")
        initial_by_id = {row.guild_uid: row for row in initial.rows}
        returned = {
            (name, row.guild_uid): row for name, lookup in results.items() for row in lookup.guilds
        }
        async with self._write_lock:
            current = await self._state()
            stale_ids = set(current.stale_ids)
            rows, failed, updated = [], 0, 0
            for row in current.rows:
                if initial_by_id.get(row.guild_uid) == row:
                    newer = returned.get((row.name, row.guild_uid))
                    if newer is None:
                        stale_ids.add(row.guild_uid)
                        failed += 1
                    else:
                        stale_ids.discard(row.guild_uid)
                        row = newer
                        updated += 1
                rows.append(row)
            snapshot = GuildSnapshot(tuple(rows), frozenset(stale_ids))
            if snapshot != current:
                await self._publish(snapshot)
        if not rows:
            return GuildReply("empty")
        if failed:
            return GuildReply("partial" if updated else "unavailable", failed=failed)
        return GuildReply("updated")
