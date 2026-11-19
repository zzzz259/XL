"""Group-scoped custom keyword entries and recording drafts."""

from __future__ import annotations

import json
import sqlite3
import unicodedata
from collections.abc import Callable, Iterator, Sequence
from contextlib import closing, contextmanager
from dataclasses import dataclass
from pathlib import Path
from uuid import uuid4

from .groups import has_group_management_role

DRAFT_IDLE_SECONDS = 600

_SCHEMA = (
    """
    CREATE TABLE keyword_entries (
        group_openid TEXT NOT NULL,
        keyword_key TEXT NOT NULL,
        keyword_label TEXT NOT NULL,
        creator_openid TEXT NOT NULL,
        media_id TEXT NOT NULL UNIQUE,
        replies_json TEXT NOT NULL,
        created_at INTEGER NOT NULL,
        PRIMARY KEY (group_openid, keyword_key)
    )
    """,
    """
    CREATE TABLE keyword_drafts (
        draft_id TEXT NOT NULL PRIMARY KEY,
        group_openid TEXT NOT NULL,
        member_openid TEXT NOT NULL,
        keyword_key TEXT NOT NULL,
        keyword_label TEXT NOT NULL,
        expires_at INTEGER NOT NULL,
        UNIQUE (group_openid, member_openid)
    )
    """,
    """
    CREATE TABLE keyword_draft_messages (
        draft_id TEXT NOT NULL REFERENCES keyword_drafts(draft_id) ON DELETE CASCADE,
        message_id TEXT NOT NULL,
        position INTEGER NOT NULL,
        parts_json TEXT NOT NULL,
        PRIMARY KEY (draft_id, message_id),
        UNIQUE (draft_id, position)
    )
    """,
)


def normalize_keyword(label: str) -> str:
    return unicodedata.normalize("NFC", label.strip()).casefold()


@dataclass(frozen=True)
class KeywordDraft:
    draft_id: str
    group_openid: str
    member_openid: str
    keyword_key: str
    label: str
    expires_at: int


@dataclass(frozen=True)
class KeywordEntry:
    label: str
    creator_openid: str
    media_id: str
    parts: tuple[dict[str, str], ...]


class KeywordStore:
    def __init__(self, data_dir: str | Path) -> None:
        self.data_dir = Path(data_dir)
        self.data_dir.mkdir(parents=True, exist_ok=True, mode=0o700)
        self.path = self.data_dir / "keywords.sqlite3"
        with self._connect(write=True) as db:
            version = db.execute("PRAGMA user_version").fetchone()[0]
            if version == 0:
                for statement in _SCHEMA:
                    db.execute(statement)
                db.execute("PRAGMA user_version = 1")
            elif version != 1:
                raise ValueError(f"unsupported keyword database schema: {version}")

    @contextmanager
    def _connect(self, *, write: bool = False) -> Iterator[sqlite3.Connection]:
        with closing(sqlite3.connect(self.path)) as db, db:
            db.execute("PRAGMA foreign_keys = ON")
            if write:
                db.execute("BEGIN IMMEDIATE")
            yield db

    def start(
        self, group_openid: str, member_openid: str, label: str, now: int
    ) -> tuple[str, KeywordDraft | None]:
        key = normalize_keyword(label)
        with self._connect(write=True) as db:
            db.execute(
                "DELETE FROM keyword_drafts WHERE group_openid = ? AND member_openid = ? AND expires_at <= ?",
                (group_openid, member_openid, now),
            )
            if db.execute(
                "SELECT 1 FROM keyword_drafts WHERE group_openid = ? AND member_openid = ?",
                (group_openid, member_openid),
            ).fetchone():
                return "busy", None
            if db.execute(
                "SELECT 1 FROM keyword_entries WHERE group_openid = ? AND keyword_key = ?",
                (group_openid, key),
            ).fetchone():
                return "exists", None
            draft = KeywordDraft(
                uuid4().hex, group_openid, member_openid, key, label.strip(),
                now + DRAFT_IDLE_SECONDS,
            )
            db.execute(
                "INSERT INTO keyword_drafts VALUES (?, ?, ?, ?, ?, ?)",
                (draft.draft_id, group_openid, member_openid, key, draft.label, draft.expires_at),
            )
            return "started", draft

    def get_draft(self, group_openid: str, member_openid: str) -> KeywordDraft | None:
        with self._connect() as db:
            row = db.execute(
                "SELECT * FROM keyword_drafts WHERE group_openid = ? AND member_openid = ?",
                (group_openid, member_openid),
            ).fetchone()
        return KeywordDraft(*row) if row is not None else None

    def has_message(self, draft: KeywordDraft, message_id: str) -> bool:
        with self._connect() as db:
            return self._has_message(db, draft.draft_id, message_id)

    @staticmethod
    def _has_message(db: sqlite3.Connection, draft_id: str, message_id: str) -> bool:
        return db.execute(
            "SELECT 1 FROM keyword_draft_messages WHERE draft_id = ? AND message_id = ?",
            (draft_id, message_id),
        ).fetchone() is not None

    def append(
        self, draft: KeywordDraft, message_id: str, parts: Sequence[dict[str, str]], now: int,
        *, completed_at: int | None = None,
    ) -> str:
        with self._connect(write=True) as db:
            if not self._active(db, draft, now):
                return "inactive"
            if self._has_message(db, draft.draft_id, message_id):
                return "duplicate"
            db.execute(
                """
                INSERT INTO keyword_draft_messages (draft_id, message_id, position, parts_json)
                SELECT ?, ?, COALESCE(MAX(position), 0) + 1, ?
                FROM keyword_draft_messages WHERE draft_id = ?
                """,
                (draft.draft_id, message_id, json.dumps(parts, ensure_ascii=False), draft.draft_id),
            )
            expires_at = max(now, completed_at if completed_at is not None else now) + DRAFT_IDLE_SECONDS
            db.execute(
                "UPDATE keyword_drafts SET expires_at = ? WHERE draft_id = ?",
                (expires_at, draft.draft_id),
            )
            return "added"

    @staticmethod
    def _active(db: sqlite3.Connection, draft: KeywordDraft, now: int) -> bool:
        return db.execute(
            """
            SELECT 1 FROM keyword_drafts
            WHERE draft_id = ? AND group_openid = ? AND member_openid = ? AND expires_at > ?
            """,
            (draft.draft_id, draft.group_openid, draft.member_openid, now),
        ).fetchone() is not None

    @staticmethod
    def _contents(db: sqlite3.Connection, draft_id: str) -> tuple[dict[str, str], ...]:
        rows = db.execute(
            "SELECT parts_json FROM keyword_draft_messages WHERE draft_id = ? ORDER BY position",
            (draft_id,),
        )
        return tuple(part for (payload,) in rows for part in json.loads(payload))

    def contents(self, draft: KeywordDraft) -> tuple[dict[str, str], ...]:
        with self._connect() as db:
            return self._contents(db, draft.draft_id)

    def publish(
        self,
        draft: KeywordDraft,
        now: int,
        prepare: Callable[[tuple[dict[str, str], ...]], Sequence[dict[str, str]]],
    ) -> str:
        with self._connect(write=True) as db:
            if not self._active(db, draft, now):
                return "inactive"
            parts = self._contents(db, draft.draft_id)
            if not parts:
                return "empty"
            cursor = db.execute(
                """
                INSERT INTO keyword_entries VALUES (?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT (group_openid, keyword_key) DO NOTHING
                """,
                (
                    draft.group_openid, draft.keyword_key, draft.label, draft.member_openid,
                    draft.draft_id, json.dumps(prepare(parts), ensure_ascii=False), now,
                ),
            )
            if cursor.rowcount == 0:
                return "exists"
            db.execute("DELETE FROM keyword_drafts WHERE draft_id = ?", (draft.draft_id,))
            return "saved"

    def cancel(self, draft: KeywordDraft) -> bool:
        with self._connect(write=True) as db:
            return db.execute(
                "DELETE FROM keyword_drafts WHERE draft_id = ? AND group_openid = ? AND member_openid = ?",
                (draft.draft_id, draft.group_openid, draft.member_openid),
            ).rowcount == 1

    def expire(self, now: int) -> tuple[str, ...]:
        with self._connect(write=True) as db:
            expired = tuple(
                draft_id for (draft_id,) in db.execute(
                    "SELECT draft_id FROM keyword_drafts WHERE expires_at <= ?", (now,)
                )
            )
            db.execute("DELETE FROM keyword_drafts WHERE expires_at <= ?", (now,))
            return expired

    def referenced_media(self) -> set[str]:
        with self._connect() as db:
            return {
                media_id for (media_id,) in db.execute(
                    "SELECT media_id FROM keyword_entries UNION SELECT draft_id FROM keyword_drafts"
                )
            }

    def list_labels(self, group_openid: str) -> tuple[str, ...]:
        with self._connect() as db:
            rows = db.execute(
                """
                SELECT keyword_label FROM keyword_entries
                WHERE group_openid = ? ORDER BY keyword_key
                """,
                (group_openid,),
            )
            return tuple(label for (label,) in rows)

    def lookup(self, group_openid: str, label: str) -> KeywordEntry | None:
        with self._connect() as db:
            row = db.execute(
                """
                SELECT keyword_label, creator_openid, media_id, replies_json
                FROM keyword_entries WHERE group_openid = ? AND keyword_key = ?
                """,
                (group_openid, normalize_keyword(label)),
            ).fetchone()
        if row is None:
            return None
        keyword_label, creator_openid, media_id, replies = row
        return KeywordEntry(keyword_label, creator_openid, media_id, tuple(json.loads(replies)))

    def delete(
        self, group_openid: str, label: str, member_openid: str, member_role: str
    ) -> tuple[str, str | None]:
        key = normalize_keyword(label)
        with self._connect(write=True) as db:
            row = db.execute(
                "SELECT creator_openid, media_id FROM keyword_entries WHERE group_openid = ? AND keyword_key = ?",
                (group_openid, key),
            ).fetchone()
            if row is None:
                return "missing", None
            creator_openid, media_id = row
            if member_openid != creator_openid and not has_group_management_role(member_role):
                return "forbidden", None
            db.execute(
                "DELETE FROM keyword_entries WHERE group_openid = ? AND keyword_key = ?",
                (group_openid, key),
            )
            return "deleted", media_id
