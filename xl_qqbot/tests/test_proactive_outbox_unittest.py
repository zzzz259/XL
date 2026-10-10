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


if __name__ == "__main__":
    unittest.main()
