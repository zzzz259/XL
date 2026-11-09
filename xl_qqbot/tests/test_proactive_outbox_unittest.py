import hashlib
import sqlite3
import tempfile
import unittest
from pathlib import Path

from bot_app.proactive_outbox import ProactiveOutbox


class ProactiveOutboxTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.data_dir = Path(self.temp_dir.name) / "persistent-data"
        self.outbox = ProactiveOutbox(self.data_dir)

    def tearDown(self):
        self.temp_dir.cleanup()

    def test_duplicate_stable_key_returns_existing_row(self):
        first_id = self.outbox.enqueue_text("game:update-1", "group-a", 0, "开始")
        duplicate_id = self.outbox.enqueue_text("game:update-1", "group-a", 0, "开始")

        self.assertEqual(duplicate_id, first_id)
        self.assertEqual(self.outbox.count(), 1)

    def test_event_status_counts_sent_and_pending_rows_without_exposing_content(self):
        sent_id = self.outbox.enqueue_text(
            "deployment:tx-start", "group-a", 0, "开始"
        )
        self.outbox.enqueue_text("deployment:tx-start", "group-b", 0, "开始")
        self.outbox.mark_sent(sent_id)

        self.assertEqual(
            self.outbox.event_status("deployment:tx-start"),
            {"total": 2, "sent": 1, "pending": 1},
        )
        self.assertEqual(
            self.outbox.event_status("deployment:unknown"),
            {"total": 0, "sent": 0, "pending": 0},
        )


    def test_insertion_sequence_is_global_and_monotonic(self):
        first_id = self.outbox.enqueue_text("event-1", "group-a", 0, "A1")
        second_id = self.outbox.enqueue_text("event-1", "group-b", 0, "B1")
        third_id = self.outbox.enqueue_text("event-2", "group-a", 0, "A2")

        self.assertLess(first_id, second_id)
        self.assertLess(second_id, third_id)
        self.assertEqual(
            [message.sequence for message in self.outbox.next_due_heads(now=0)],
            [first_id, second_id],
        )

    def test_only_oldest_pending_row_per_group_is_selected(self):
        first_id = self.outbox.enqueue_text("event-1", "group-a", 0, "A1")
        self.outbox.enqueue_text("event-2", "group-a", 0, "A2")
        other_group_id = self.outbox.enqueue_text("event-3", "group-b", 0, "B1")

        heads = self.outbox.next_due_heads(now=0)

        self.assertEqual(
            [(message.sequence, message.recipient) for message in heads],
            [(first_id, "group-a"), (other_group_id, "group-b")],
        )

    def test_failed_group_head_blocks_later_row_but_not_other_groups(self):
        first_id = self.outbox.enqueue_text("event-1", "group-a", 0, "A1")
        later_id = self.outbox.enqueue_text("event-2", "group-a", 0, "A2")
        other_group_id = self.outbox.enqueue_text("event-3", "group-b", 0, "B1")
        self.outbox.mark_failed(first_id, retry_at=100)

        due_heads = self.outbox.next_due_heads(now=50)

        self.assertEqual([message.sequence for message in due_heads], [other_group_id])
        self.assertNotIn(later_id, [message.sequence for message in due_heads])

    def test_failure_persists_retry_count_and_next_attempt(self):
        row_id = self.outbox.enqueue_text("event-1", "group-a", 0, "A1")
        self.outbox.mark_failed(row_id, retry_at=100)

        self.assertEqual(self.outbox.next_due_heads(now=99), [])
        retry = self.outbox.next_due_heads(now=100)[0]
        self.assertEqual(retry.retry_count, 1)
        self.assertEqual(retry.next_attempt_at, 100)

    def test_successful_acknowledgement_releases_next_group_row(self):
        first_id = self.outbox.enqueue_text("event-1", "group-a", 0, "A1")
        second_id = self.outbox.enqueue_text("event-2", "group-a", 0, "A2")
        self.outbox.mark_sent(first_id)

        heads = self.outbox.next_due_heads(now=0)

        self.assertEqual([message.sequence for message in heads], [second_id])

    def test_database_reopen_preserves_content_order_and_retry_state(self):
        first_id = self.outbox.enqueue_text("event-1", "group-a", 0, "第一条")
        second_id = self.outbox.enqueue_text("event-2", "group-a", 0, "第二条")
        self.outbox.mark_failed(first_id, retry_at=200)

        reopened = ProactiveOutbox(self.data_dir)
        messages = reopened.list_pending()

        self.assertEqual(
            [message.sequence for message in messages], [first_id, second_id]
        )
        self.assertEqual([message.text for message in messages], ["第一条", "第二条"])
        self.assertEqual(messages[0].retry_count, 1)
        self.assertEqual(messages[0].next_attempt_at, 200)

    def test_image_attachment_is_copied_to_persistent_spool(self):
        source = Path(self.temp_dir.name) / "portrait.png"
        source.write_bytes(b"image-bytes")

        row_id = self.outbox.enqueue_image(
            "character:1001:skin-2", "group-a", 0, source, content="caption"
        )
        message = self.outbox.get(row_id)
        source.unlink()

        self.assertEqual(message.kind, "image")
        self.assertEqual(message.text, "caption")
        self.assertTrue(message.media_path)
        spool_path = self.data_dir / message.media_path
        self.assertTrue(spool_path.is_file())
        self.assertEqual(spool_path.read_bytes(), b"image-bytes")

    def test_acknowledging_image_removes_spooled_media(self):
        source = Path(self.temp_dir.name) / "portrait.png"
        source.write_bytes(b"image-bytes")
        row_id = self.outbox.enqueue_image("character:1", "group-a", 0, source)
        spool_path = self.data_dir / self.outbox.get(row_id).media_path

        self.outbox.mark_sent(row_id)

        self.assertFalse(spool_path.exists())
        self.assertEqual(self.outbox.get(row_id).status, "sent")

    def test_replay_after_sent_image_cleanup_is_idempotent(self):
        source = Path(self.temp_dir.name) / "portrait.png"
        source.write_bytes(b"image-bytes")
        row_id = self.outbox.enqueue_image("character:1", "group-a", 0, source)
        self.outbox.mark_sent(row_id)

        self.assertEqual(
            self.outbox.enqueue_image("character:1", "group-a", 0, source),
            row_id,
        )

    def test_image_idempotency_rejects_changed_caption(self):
        source = Path(self.temp_dir.name) / "portrait.png"
        source.write_bytes(b"image-bytes")
        self.outbox.enqueue_image(
            "character:1", "group-a", 0, source, content="old caption"
        )

        with self.assertRaises(ValueError):
            self.outbox.enqueue_image(
                "character:1", "group-a", 0, source, content="new caption"
            )


PNG_MAGIC = b"\x89PNG\r\n\x1a\n"


class LocalMultipartOutboxTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.data_dir = Path(self.temp_dir.name) / "persistent-data"
        self.outbox = ProactiveOutbox(self.data_dir)

    def tearDown(self):
        self.temp_dir.cleanup()

    def make_png(self, tag: str) -> tuple[Path, str]:
        body = PNG_MAGIC + tag.encode("utf-8")
        path = Path(self.temp_dir.name) / f"{tag}.png"
        path.write_bytes(body)
        return path, hashlib.sha256(body).hexdigest()

    def test_local_multipart_row_stores_mode_and_expected_hash(self):
        source, sha256 = self.make_png("portrait-1")

        row_id = self.outbox.enqueue_image(
            "game-portrait:1:a", "group-a", 0, source,
            content="caption", image_delivery_mode="local_multipart",
            expected_sha256=sha256,
        )
        message = self.outbox.get(row_id)

        self.assertEqual(message.image_delivery_mode, "local_multipart")
        self.assertEqual(message.media_sha256, sha256)
        self.assertEqual((self.data_dir / message.media_path).read_bytes(), PNG_MAGIC + b"portrait-1")

    def test_local_multipart_rejects_bad_magic_or_sha_mismatch(self):
        source, _ = self.make_png("portrait-2")
        with self.assertRaises(ValueError):
            self.outbox.enqueue_image(
                "game-portrait:1:b", "group-a", 0, source,
                image_delivery_mode="local_multipart", expected_sha256="ff" * 32,
            )
        not_png = Path(self.temp_dir.name) / "fake.png"
        not_png.write_bytes(b"not a png")
        with self.assertRaises(ValueError):
            self.outbox.enqueue_image(
                "game-portrait:1:b", "group-a", 0, not_png,
                image_delivery_mode="local_multipart",
                expected_sha256=hashlib.sha256(b"not a png").hexdigest(),
            )
        self.assertEqual(self.outbox.count(), 0)

    def test_local_multipart_requires_sha_and_rejects_malformed_or_legacy_mix(self):
        source, sha256 = self.make_png("portrait-3")
        with self.assertRaises(ValueError):
            self.outbox.enqueue_image(
                "game-portrait:1:c", "group-a", 0, source,
                image_delivery_mode="local_multipart",
            )
        with self.assertRaises(ValueError):
            self.outbox.enqueue_image(
                "game-portrait:1:c", "group-a", 0, source,
                image_delivery_mode="local_multipart", expected_sha256="xyz",
            )
        with self.assertRaises(ValueError):
            self.outbox.enqueue_image(
                "game-portrait:1:c", "group-a", 0, source,
                image_delivery_mode="boom", expected_sha256=sha256,
            )
        with self.assertRaises(ValueError):
            self.outbox.enqueue_image(
                "game-portrait:1:c", "group-a", 0, source,
                expected_sha256=sha256,
            )

    def test_local_multipart_idempotent_replay_and_content_change_rejected(self):
        source, sha256 = self.make_png("portrait-4")
        row_id = self.outbox.enqueue_image(
            "game-portrait:1:d", "group-a", 0, source,
            image_delivery_mode="local_multipart", expected_sha256=sha256,
        )
        replay = self.outbox.enqueue_image(
            "game-portrait:1:d", "group-a", 0, source,
            image_delivery_mode="local_multipart", expected_sha256=sha256,
        )
        self.assertEqual(replay, row_id)
        self.assertEqual(self.outbox.count(), 1)
        other, other_sha = self.make_png("portrait-4b")
        with self.assertRaises(ValueError):
            self.outbox.enqueue_image(
                "game-portrait:1:d", "group-a", 0, other,
                image_delivery_mode="local_multipart", expected_sha256=other_sha,
            )
        legacy_png, _ = self.make_png("portrait-4c")
        with self.assertRaises(ValueError):
            self.outbox.enqueue_image(
                "game-portrait:1:d", "group-a", 0, legacy_png,
            )

    def test_database_without_mode_column_migrates_and_old_rows_default_legacy(self):
        legacy_dir = Path(self.temp_dir.name) / "legacy-data"
        legacy_dir.mkdir()
        database_path = legacy_dir / "proactive_outbox.sqlite3"
        connection = sqlite3.connect(database_path)
        try:
            connection.execute(
                """
                CREATE TABLE messages (
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
                "INSERT INTO messages (event_key, recipient, ordinal, kind, text, created_at) "
                "VALUES ('legacy:event', 'group-a', 0, 'text', '旧公告', 1.0)"
            )
            connection.commit()
        finally:
            connection.close()
        (legacy_dir / "proactive_outbox_media").mkdir(exist_ok=True)

        reopened = ProactiveOutbox(legacy_dir)

        legacy = reopened.find_by_key("legacy:event", "group-a", 0)
        self.assertIsNotNone(legacy)
        self.assertEqual(legacy.image_delivery_mode, "legacy")
        self.assertEqual(legacy.text, "旧公告")
        source, sha256 = self.make_png("portrait-migrated")
        row_id = reopened.enqueue_image(
            "game-portrait:1:m", "group-a", 0, source,
            image_delivery_mode="local_multipart", expected_sha256=sha256,
        )
        self.assertEqual(reopened.get(row_id).image_delivery_mode, "local_multipart")
        # 迁移后旧行仍可走既有 legacy 发送语义
        legacy_png, _ = self.make_png("portrait-migrated-legacy")
        legacy_row = reopened.enqueue_image(
            "legacy:image", "group-a", 0, legacy_png,
        )
        self.assertEqual(reopened.get(legacy_row).image_delivery_mode, "legacy")

    def test_mode_survives_reopen(self):
        source, sha256 = self.make_png("portrait-5")
        row_id = self.outbox.enqueue_image(
            "game-portrait:1:e", "group-a", 0, source,
            image_delivery_mode="local_multipart", expected_sha256=sha256,
        )

        reopened = ProactiveOutbox(self.data_dir)

        self.assertEqual(reopened.get(row_id).image_delivery_mode, "local_multipart")


if __name__ == "__main__":
    unittest.main()
