import hashlib
import tempfile
import unittest
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

from bot_app.proactive_dispatcher import ProactiveDispatcher, is_quiet_hours
from bot_app.proactive_outbox import ProactiveOutbox

TZ = ZoneInfo("Asia/Shanghai")


class RecordingSender:
    def __init__(self, failures=()):
        self.failures = set(failures)
        self.text_messages = []
        self.image_messages = []
        self.schedule_image_messages = []

    async def send_text(self, group, text):
        self.text_messages.append((group, text))
        return group not in self.failures

    async def send_image(self, path, content, groups):
        self.image_messages.append((path, content, tuple(groups)))
        return {group: group not in self.failures for group in groups}

    async def send_schedule_image(self, path, content, groups):
        self.schedule_image_messages.append((path, content, tuple(groups)))
        return {group: group not in self.failures for group in groups}


def local_time(hour, minute, second=0):
    return datetime(2026, 9, 29, hour, minute, second, tzinfo=TZ)


class ProactiveDispatcherTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.outbox = ProactiveOutbox(Path(self.temp_dir.name) / "data")

    def tearDown(self):
        self.temp_dir.cleanup()

    def test_quiet_hours_boundary_in_asia_shanghai(self):
        self.assertFalse(is_quiet_hours(local_time(1, 59, 59)))
        self.assertTrue(is_quiet_hours(local_time(2, 0, 0)))
        self.assertTrue(is_quiet_hours(local_time(7, 59, 59)))
        self.assertFalse(is_quiet_hours(local_time(8, 0, 0)))

    async def test_queue_is_held_at_night_and_drained_at_eight(self):
        self.outbox.enqueue_text("bili:1", "group-a", 0, "更新")
        sender = RecordingSender()
        dispatcher = ProactiveDispatcher(
            self.outbox, sender, now=lambda: local_time(3, 0)
        )

        self.assertEqual(await dispatcher.dispatch_once(), 0)
        self.assertEqual(sender.text_messages, [])
        self.assertEqual(self.outbox.count(), 1)

        dispatcher = ProactiveDispatcher(
            self.outbox, sender, now=lambda: local_time(8, 0)
        )
        self.assertEqual(await dispatcher.dispatch_once(), 1)
        self.assertEqual(sender.text_messages, [("group-a", "更新")])
        self.assertEqual(self.outbox.count(), 0)

    async def test_rechecks_quiet_gate_immediately_before_network_send(self):
        self.outbox.enqueue_text("bili:1", "group-a", 0, "更新")
        sender = RecordingSender()
        times = iter((local_time(1, 59, 59), local_time(2, 0, 0)))
        dispatcher = ProactiveDispatcher(self.outbox, sender, now=lambda: next(times))

        self.assertEqual(await dispatcher.dispatch_once(), 0)
        self.assertEqual(sender.text_messages, [])
        self.assertEqual(self.outbox.count(), 1)

    async def test_failed_group_does_not_block_other_groups(self):
        self.outbox.enqueue_text("event-a", "group-a", 0, "A")
        self.outbox.enqueue_text("event-b", "group-b", 0, "B")
        sender = RecordingSender(failures={"group-a"})
        dispatcher = ProactiveDispatcher(
            self.outbox, sender, now=lambda: local_time(10, 0)
        )

        self.assertEqual(await dispatcher.dispatch_once(), 1)
        self.assertEqual(sender.text_messages, [("group-a", "A"), ("group-b", "B")])
        pending = self.outbox.list_pending()
        self.assertEqual(
            [(item.recipient, item.retry_count) for item in pending], [("group-a", 1)]
        )

    async def test_image_dispatch_uses_durable_relative_spool_path(self):
        source = Path(self.temp_dir.name) / "image.png"
        source.write_bytes(b"image")
        row_id = self.outbox.enqueue_image(
            "game:char:1", "group-a", 0, source, content="caption"
        )
        message = self.outbox.get(row_id)
        sender = RecordingSender()
        dispatcher = ProactiveDispatcher(
            self.outbox, sender, now=lambda: local_time(10, 0)
        )

        self.assertEqual(await dispatcher.dispatch_once(), 1)
        self.assertEqual(
            sender.image_messages,
            [
                (
                    str(self.outbox.data_dir / message.media_path),
                    "caption",
                    ("group-a",),
                )
            ],
        )
        self.assertEqual(sender.schedule_image_messages, [])
        self.assertFalse((self.outbox.data_dir / message.media_path).exists())


class LocalMultipartDispatcherTests(unittest.IsolatedAsyncioTestCase):
    PNG_MAGIC = b"\x89PNG\r\n\x1a\n"

    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.outbox = ProactiveOutbox(Path(self.temp_dir.name) / "data")

    def tearDown(self):
        self.temp_dir.cleanup()

    def make_png(self, tag: str) -> tuple[Path, str]:
        body = self.PNG_MAGIC + tag.encode("utf-8")
        path = Path(self.temp_dir.name) / f"{tag}.png"
        path.write_bytes(body)
        return path, hashlib.sha256(body).hexdigest()

    def enqueue_portrait(self, tag: str = "portrait") -> int:
        source, sha256 = self.make_png(tag)
        return self.outbox.enqueue_image(
            f"game-portrait:1:{tag}", "group-a", 0, source,
            content="【星落】雾铃 立绘（1）",
            image_delivery_mode="local_multipart", expected_sha256=sha256,
        )

    async def test_local_multipart_dispatches_via_send_local_image(self):
        class LocalImageSender(RecordingSender):
            def __init__(self):
                super().__init__()
                self.local_image_messages = []

            async def send_local_image(self, path, content, groups):
                self.local_image_messages.append((path, content, tuple(groups)))
                return {group: True for group in groups}

        row_id = self.enqueue_portrait("via-local")
        message = self.outbox.get(row_id)
        sender = LocalImageSender()
        dispatcher = ProactiveDispatcher(
            self.outbox, sender, now=lambda: local_time(10, 0)
        )

        self.assertEqual(await dispatcher.dispatch_once(), 1)
        self.assertEqual(
            sender.local_image_messages,
            [
                (
                    str(self.outbox.data_dir / message.media_path),
                    "【星落】雾铃 立绘（1）",
                    ("group-a",),
                )
            ],
        )
        # 即使配置了 file_base_url 语义，也不会走公网 URL 路径
        self.assertEqual(sender.image_messages, [])
        self.assertEqual(sender.schedule_image_messages, [])

    async def test_local_multipart_falls_back_to_schedule_image_for_multipart(self):
        row_id = self.enqueue_portrait("via-schedule")
        message = self.outbox.get(row_id)
        sender = RecordingSender()
        dispatcher = ProactiveDispatcher(
            self.outbox, sender, now=lambda: local_time(10, 0)
        )

        self.assertEqual(await dispatcher.dispatch_once(), 1)
        self.assertEqual(sender.image_messages, [])
        self.assertEqual(
            sender.schedule_image_messages,
            [
                (
                    str(self.outbox.data_dir / message.media_path),
                    "【星落】雾铃 立绘（1）",
                    ("group-a",),
                )
            ],
        )

    async def test_corrupt_spool_fails_and_retries(self):
        row_id = self.enqueue_portrait("corrupt")
        spool_path = self.outbox.data_dir / self.outbox.get(row_id).media_path
        spool_path.write_bytes(b"\x89PNG\r\n\x1a\ntampered-bytes")
        sender = RecordingSender()
        dispatcher = ProactiveDispatcher(
            self.outbox, sender, now=lambda: local_time(10, 0)
        )

        self.assertEqual(await dispatcher.dispatch_once(), 0)
        self.assertEqual(sender.schedule_image_messages, [])
        pending = self.outbox.list_pending()
        self.assertEqual(len(pending), 1)
        self.assertEqual(pending[0].retry_count, 1)

    async def test_local_multipart_held_during_quiet_hours(self):
        self.enqueue_portrait("quiet")
        sender = RecordingSender()
        dispatcher = ProactiveDispatcher(
            self.outbox, sender, now=lambda: local_time(3, 0)
        )

        self.assertEqual(await dispatcher.dispatch_once(), 0)
        self.assertEqual(sender.schedule_image_messages, [])
        self.assertEqual(self.outbox.count(), 1)

    async def test_legacy_rows_keep_existing_send_image_path(self):
        source = Path(self.temp_dir.name) / "card.png"
        source.write_bytes(b"card")
        self.outbox.enqueue_image("game-card:v1:1", "group-a", 0, source, content="图鉴")
        sender = RecordingSender()
        dispatcher = ProactiveDispatcher(
            self.outbox, sender, now=lambda: local_time(10, 0)
        )

        self.assertEqual(await dispatcher.dispatch_once(), 1)
        self.assertEqual(len(sender.image_messages), 1)
        self.assertEqual(sender.schedule_image_messages, [])


if __name__ == "__main__":
    unittest.main()
