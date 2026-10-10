"""Durable storage primitives for proactive QQ messages."""

from __future__ import annotations

import hashlib
import os
import sqlite3
import tempfile
import time
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path

PathLike = str | os.PathLike[str]


@dataclass(frozen=True)
class OutboxMessage:
    sequence: int
    event_key: str
    recipient: str
    ordinal: int
    kind: str
    text: str | None
    media_path: str | None
    media_sha256: str | None
    retry_count: int
    next_attempt_at: float
    created_at: float
    status: str


class ProactiveOutbox:
    """A persistent, idempotent message queue with a durable media spool."""

    def __init__(self, data_dir: PathLike):
        self.data_dir = Path(data_dir)
        self.data_dir.mkdir(parents=True, exist_ok=True)
        self.database_path = self.data_dir / "proactive_outbox.sqlite3"
        self.spool_dir = self.data_dir / "proactive_outbox_media"
        self.spool_dir.mkdir(parents=True, exist_ok=True)
        self._initialize()

    def enqueue_text(
        self, event_key: str, recipient: str, ordinal: int, text: str
    ) -> int:
        self._validate_key(event_key, recipient, ordinal)
        if not isinstance(text, str) or not text:
            raise ValueError("text must be a non-empty string")

        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            existing = self._find_existing(connection, event_key, recipient, ordinal)
            if existing is not None:
                if existing["kind"] != "text" or existing["text"] != text:
                    raise ValueError("idempotency key already has different content")
                return int(existing["sequence"])

            cursor = connection.execute(
                """
                INSERT INTO messages (
                    event_key, recipient, ordinal, kind, text, created_at
                ) VALUES (?, ?, ?, 'text', ?, ?)
                """,
                (event_key, recipient, ordinal, text, time.time()),
            )
            return int(cursor.lastrowid)

    def enqueue_image(
        self,
        event_key: str,
        recipient: str,
        ordinal: int,
        source_path: PathLike,
        *,
        content: str = "",
    ) -> int:
        self._validate_key(event_key, recipient, ordinal)
        source = Path(source_path)
        if not source.is_file():
            raise FileNotFoundError(source)
        if not isinstance(content, str):
            raise TypeError("image content must be a string")
        stored_content = content or None

        existing = self._find_existing_key(event_key, recipient, ordinal)
        if existing is not None:
            if (
                existing.kind != "image"
                or existing.media_sha256 != self._file_hash(source)
                or existing.text != stored_content
            ):
                raise ValueError("idempotency key already has different content")
            if existing.status == "sent":
                return existing.sequence
            if (
                not existing.media_path
                or not (self.data_dir / existing.media_path).is_file()
                or self._file_hash(self.data_dir / existing.media_path)
                != existing.media_sha256
            ):
                raise ValueError("idempotency key refers to missing or corrupt media")
            return existing.sequence

        temporary_path, content_hash = self._copy_to_temporary_spool(source)
        spool_name = self._spool_name(
            event_key, recipient, ordinal, content_hash, source
        )
        final_path = self.spool_dir / spool_name
        relative_path = (Path(self.spool_dir.name) / spool_name).as_posix()
        created_final = False

        try:
            try:
                os.link(temporary_path, final_path)
                created_final = True
            except FileExistsError:
                if self._file_hash(final_path) != content_hash:
                    raise OSError("existing spool file does not match its content hash")
            finally:
                temporary_path.unlink(missing_ok=True)

            with self._connect() as connection:
                connection.execute("BEGIN IMMEDIATE")
                existing = self._find_existing(
                    connection, event_key, recipient, ordinal
                )
                if existing is not None:
                    existing_path = existing["media_path"]
                    if (
                        existing["kind"] != "image"
                        or existing["text"] != stored_content
                        or existing["media_sha256"] != content_hash
                        or not existing_path
                        or not (self.data_dir / existing_path).is_file()
                        or self._file_hash(self.data_dir / existing_path)
                        != content_hash
                    ):
                        raise ValueError(
                            "idempotency key already has different or missing media"
                        )
                    return int(existing["sequence"])

                cursor = connection.execute(
                    """
                    INSERT INTO messages (
                        event_key, recipient, ordinal, kind, text, media_path,
                        media_sha256, created_at
                    ) VALUES (?, ?, ?, 'image', ?, ?, ?, ?)
                    """,
                    (
                        event_key,
                        recipient,
                        ordinal,
                        stored_content,
                        relative_path,
                        content_hash,
                        time.time(),
                    ),
                )
                return int(cursor.lastrowid)
        except Exception:
            if created_final:
                self._remove_if_unreferenced(relative_path)
            raise
        finally:
            temporary_path.unlink(missing_ok=True)

    def next_due_heads(self, now: float | None = None) -> list[OutboxMessage]:
        """Return due oldest-pending messages, at most one per recipient."""
        current_time = time.time() if now is None else float(now)
        with self._connect() as connection:
            rows = connection.execute(
                """
                SELECT message.*
                FROM messages AS message
                WHERE message.status = 'pending'
                  AND NOT EXISTS (
                      SELECT 1
                      FROM messages AS older
                      WHERE older.recipient = message.recipient
                        AND older.status = 'pending'
                        AND older.sequence < message.sequence
                  )
                  AND message.next_attempt_at <= ?
                ORDER BY message.sequence
                """,
                (current_time,),
            ).fetchall()
        return [self._to_message(row) for row in rows]

    def mark_sent(self, row_id: int) -> None:
        media_path = None
        with self._connect() as connection:
            cursor = connection.execute(
                """
                UPDATE messages
                SET status = 'sent', sent_at = ?
                WHERE sequence = ? AND status = 'pending'
                """,
                (time.time(), row_id),
            )
            row = connection.execute(
                "SELECT media_path FROM messages WHERE sequence = ?", (row_id,)
            ).fetchone()
            if row is not None:
                media_path = row["media_path"]
            if cursor.rowcount == 0:
                status_row = connection.execute(
                    "SELECT status FROM messages WHERE sequence = ?", (row_id,)
                ).fetchone()
                if status_row is None:
                    raise KeyError(f"unknown outbox row: {row_id}")
                if status_row["status"] != "sent":
                    raise RuntimeError(f"cannot acknowledge outbox row: {row_id}")
        if media_path:
            self._remove_if_unreferenced(media_path, pending_only=True)

    def mark_failed(self, row_id: int, retry_at: float) -> None:
        with self._connect() as connection:
            cursor = connection.execute(
                """
                UPDATE messages
                SET retry_count = retry_count + 1, next_attempt_at = ?
                WHERE sequence = ? AND status = 'pending'
                """,
                (float(retry_at), row_id),
            )
            if cursor.rowcount == 0:
                row = connection.execute(
                    "SELECT status FROM messages WHERE sequence = ?", (row_id,)
                ).fetchone()
                if row is None:
                    raise KeyError(f"unknown outbox row: {row_id}")
                if row["status"] != "pending":
                    raise RuntimeError(f"cannot retry non-pending outbox row: {row_id}")

    def get(self, row_id: int) -> OutboxMessage:
        with self._connect() as connection:
            row = connection.execute(
                "SELECT * FROM messages WHERE sequence = ?", (row_id,)
            ).fetchone()
        if row is None:
            raise KeyError(f"unknown outbox row: {row_id}")
        return self._to_message(row)

    def list_pending(self) -> list[OutboxMessage]:
        with self._connect() as connection:
            rows = connection.execute(
                "SELECT * FROM messages WHERE status = 'pending' ORDER BY sequence"
            ).fetchall()
        return [self._to_message(row) for row in rows]

    def count(self) -> int:
        with self._connect() as connection:
            row = connection.execute(
                "SELECT COUNT(*) AS total FROM messages WHERE status = 'pending'"
            ).fetchone()
        return int(row["total"])

    def health(self) -> dict:
        return {
            "ok": self.database_path.is_file(),
            "pending_count": self.count(),
            "database_path": str(self.database_path),
        }

    def _initialize(self) -> None:
        with self._connect() as connection:
            connection.execute("PRAGMA journal_mode = WAL")
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS messages (
                    sequence INTEGER PRIMARY KEY AUTOINCREMENT,
                    event_key TEXT NOT NULL,
                    recipient TEXT NOT NULL,
                    ordinal INTEGER NOT NULL CHECK (ordinal >= 0),
                    kind TEXT NOT NULL CHECK (kind IN ('text', 'image')),
                    text TEXT,
                    media_path TEXT,
                    media_sha256 TEXT,
                    retry_count INTEGER NOT NULL DEFAULT 0 CHECK (retry_count >= 0),
                    next_attempt_at REAL NOT NULL DEFAULT 0,
                    created_at REAL NOT NULL,
                    sent_at REAL,
                    status TEXT NOT NULL DEFAULT 'pending'
                        CHECK (status IN ('pending', 'sent')),
                    UNIQUE (event_key, recipient, ordinal),
                    CHECK (
                        (kind = 'text' AND text IS NOT NULL AND media_path IS NULL)
                        OR
                        (kind = 'image' AND media_path IS NOT NULL)
                    )
                )
                """
            )
            connection.execute(
                "CREATE INDEX IF NOT EXISTS pending_sequence_idx "
                "ON messages(status, sequence)"
            )
            connection.execute(
                "CREATE INDEX IF NOT EXISTS recipient_pending_idx "
                "ON messages(recipient, status, sequence)"
            )

    @contextmanager
    def _connect(self) -> Iterator[sqlite3.Connection]:
        connection = sqlite3.connect(self.database_path, timeout=30)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys = ON")
        connection.execute("PRAGMA busy_timeout = 30000")
        try:
            yield connection
        except Exception:
            connection.rollback()
            raise
        else:
            connection.commit()
        finally:
            connection.close()

    @staticmethod
    def _validate_key(event_key: str, recipient: str, ordinal: int) -> None:
        if not isinstance(event_key, str) or not event_key:
            raise ValueError("event_key must be a non-empty string")
        if not isinstance(recipient, str) or not recipient:
            raise ValueError("recipient must be a non-empty string")
        if isinstance(ordinal, bool) or not isinstance(ordinal, int) or ordinal < 0:
            raise ValueError("ordinal must be a non-negative integer")

    @staticmethod
    def _find_existing(
        connection: sqlite3.Connection,
        event_key: str,
        recipient: str,
        ordinal: int,
    ) -> sqlite3.Row | None:
        return connection.execute(
            """
            SELECT * FROM messages
            WHERE event_key = ? AND recipient = ? AND ordinal = ?
            """,
            (event_key, recipient, ordinal),
        ).fetchone()

    def _copy_to_temporary_spool(self, source: Path) -> tuple[Path, str]:
        file_descriptor, temp_name = tempfile.mkstemp(
            prefix=".incoming-", dir=self.spool_dir
        )
        temp_path = Path(temp_name)
        digest = hashlib.sha256()
        try:
            with (
                os.fdopen(file_descriptor, "wb") as destination,
                source.open("rb") as origin,
            ):
                while chunk := origin.read(1024 * 1024):
                    destination.write(chunk)
                    digest.update(chunk)
                destination.flush()
                os.fsync(destination.fileno())
            return temp_path, digest.hexdigest()
        except Exception:
            temp_path.unlink(missing_ok=True)
            raise

    @staticmethod
    def _spool_name(
        event_key: str,
        recipient: str,
        ordinal: int,
        content_hash: str,
        source: Path,
    ) -> str:
        identity = f"{event_key}\0{recipient}\0{ordinal}".encode()
        identity_hash = hashlib.sha256(identity).hexdigest()
        extension = source.suffix.lower()
        if not extension or not extension[1:].isalnum():
            extension = ".bin"
        return f"{identity_hash}-{content_hash}{extension}"

    @staticmethod
    def _file_hash(path: Path) -> str:
        digest = hashlib.sha256()
        with path.open("rb") as file_obj:
            while chunk := file_obj.read(1024 * 1024):
                digest.update(chunk)
        return digest.hexdigest()

    def _find_existing_key(
        self, event_key: str, recipient: str, ordinal: int
    ) -> OutboxMessage | None:
        with self._connect() as connection:
            row = connection.execute(
                """
                SELECT * FROM messages
                WHERE event_key = ? AND recipient = ? AND ordinal = ?
                """,
                (event_key, recipient, ordinal),
            ).fetchone()
        return self._to_message(row) if row is not None else None

    def _remove_if_unreferenced(
        self, relative_path: str, *, pending_only: bool = False
    ) -> None:
        with self._connect() as connection:
            if pending_only:
                row = connection.execute(
                    "SELECT 1 FROM messages WHERE media_path = ? AND status = 'pending' LIMIT 1",
                    (relative_path,),
                ).fetchone()
            else:
                row = connection.execute(
                    "SELECT 1 FROM messages WHERE media_path = ? LIMIT 1",
                    (relative_path,),
                ).fetchone()
        if row is None:
            (self.data_dir / relative_path).unlink(missing_ok=True)

    @staticmethod
    def _to_message(row: sqlite3.Row) -> OutboxMessage:
        return OutboxMessage(
            sequence=int(row["sequence"]),
            event_key=str(row["event_key"]),
            recipient=str(row["recipient"]),
            ordinal=int(row["ordinal"]),
            kind=str(row["kind"]),
            text=row["text"],
            media_path=row["media_path"],
            media_sha256=row["media_sha256"],
            retry_count=int(row["retry_count"]),
            next_attempt_at=float(row["next_attempt_at"]),
            created_at=float(row["created_at"]),
            status=str(row["status"]),
        )
