import json
import logging
import tempfile
import unittest
from pathlib import Path

from bot_app.config import (
    BotConfig,
    Config,
    MessageConfig,
    TargetConfig,
    UploadConfig,
    WatchConfig,
)
from bot_app.outbox import list_version_batches
from bot_app.proactive_outbox import ProactiveOutbox
from bot_app.watcher import Watcher


class NoDirectSender:
    async def send_text(self, *args, **kwargs):
        raise AssertionError("watcher must persist messages instead of sending")

    async def send_image(self, *args, **kwargs):
        raise AssertionError("watcher must persist messages instead of sending")


class PassThroughTiers:
    def filter_groups(self, _feature, groups):
        return list(groups)


class WatcherIdempotencyLoggingTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.root = Path(self.temp_dir.name)
        outbox_dir = self.root / "source-outbox"
        outbox_dir.mkdir()
        self.data_dir = self.root / "data"
        config = Config(
            bot=BotConfig(appid="1", secret="2"),
            watch=WatchConfig(
                outbox_dir=str(outbox_dir),
                interval_seconds=30,
                data_dir=str(self.data_dir),
            ),
            target=TargetConfig(
                group_openids=["group-a"], auto_learn_from_events=False
            ),
            upload=UploadConfig(file_base_url=""),
            message=MessageConfig(template="【星落】{version}-{name}"),
        )
        self.outbox = ProactiveOutbox(self.data_dir)
        self.watcher = Watcher(
            config,
            NoDirectSender(),
            tiers=PassThroughTiers(),
            proactive_outbox=self.outbox,
        )
        self.image_name = "1_A_角色档案_长图.png"
        self.version_dir = outbox_dir / "v1"
        self.version_dir.mkdir()
        (self.version_dir / self.image_name).write_bytes(b"character-card")
        (self.version_dir / "manifest.json").write_text(
            json.dumps(
                {
                    "version": "v1",
                    "characters": [
                        {"id": "1", "name": "A", "file_name": self.image_name}
                    ],
                }
            ),
            encoding="utf-8",
        )
        self.batch = list_version_batches(str(outbox_dir))[0]

    def tearDown(self):
        self.temp_dir.cleanup()

    async def test_already_sent_recipient_is_logged_without_new_enqueue(self):
        sequence = self.outbox.enqueue_image(
            "game-card:v1:1:1_A_角色档案_长图.png",
            "group-a",
            ordinal=0,
            source_path=self.version_dir / self.image_name,
            content="【星落】v1-A",
        )
        self.outbox.mark_sent(sequence)

        with self.assertLogs("bot_app.watcher", level=logging.INFO) as captured:
            await self.watcher._process_batch(self.batch)

        self.assertEqual(self.outbox.count(), 0)
        log_output = "\n".join(captured.output)
        self.assertIn("newly_queued=0", log_output)
        self.assertIn("already_sent=1", log_output)

    async def test_first_delivery_is_logged_as_newly_queued(self):
        with self.assertLogs("bot_app.watcher", level=logging.INFO) as captured:
            await self.watcher._process_batch(self.batch)

        self.assertEqual(self.outbox.count(), 1)
        log_output = "\n".join(captured.output)
        self.assertIn("newly_queued=1", log_output)
        self.assertIn("already_sent=0", log_output)
        self.assertIn("already_pending=0", log_output)

    async def test_existing_pending_recipient_is_logged_as_reused(self):
        self.outbox.enqueue_image(
            "game-card:v1:1:1_A_角色档案_长图.png",
            "group-a",
            ordinal=0,
            source_path=self.version_dir / self.image_name,
            content="【星落】v1-A",
        )

        with self.assertLogs("bot_app.watcher", level=logging.INFO) as captured:
            await self.watcher._process_batch(self.batch)

        self.assertEqual(self.outbox.count(), 1)
        log_output = "\n".join(captured.output)
        self.assertIn("newly_queued=0", log_output)
        self.assertIn("already_pending=1", log_output)


if __name__ == "__main__":
    unittest.main()
