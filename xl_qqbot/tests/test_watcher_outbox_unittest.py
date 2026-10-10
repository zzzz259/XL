import json
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
        raise AssertionError("proactive game messages must use the router outbox")

    async def send_image(self, *args, **kwargs):
        raise AssertionError("proactive game messages must use the router outbox")


class PassThroughTiers:
    def filter_groups(self, _feature, groups):
        return list(groups)


def make_config(root, groups=("g1",)):
    outbox_dir = root / "outbox"
    outbox_dir.mkdir(parents=True, exist_ok=True)
    data_dir = root / "data"
    return Config(
        bot=BotConfig(appid="1", secret="2"),
        watch=WatchConfig(
            outbox_dir=str(outbox_dir),
            interval_seconds=30,
            data_dir=str(data_dir),
        ),
        target=TargetConfig(group_openids=list(groups), auto_learn_from_events=False),
        upload=UploadConfig(file_base_url=""),
        message=MessageConfig(template="【星落】{version}-{name}"),
    )


def make_batch(root, version="v1"):
    version_dir = root / "outbox" / version
    version_dir.mkdir(parents=True)
    image_name = "1_A_角色档案_长图.png"
    (version_dir / image_name).write_bytes(b"character-card")
    (version_dir / "manifest.json").write_text(
        json.dumps(
            {
                "version": version,
                "characters": [{"id": "1", "name": "A", "file_name": image_name}],
            }
        ),
        encoding="utf-8",
    )
    return version_dir


class WatcherOutboxTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.root = Path(self.temp_dir.name)
        self.config = make_config(self.root)
        self.outbox = ProactiveOutbox(self.config.watch.data_dir)
        self.watcher = Watcher(
            self.config,
            NoDirectSender(),
            tiers=PassThroughTiers(),
            proactive_outbox=self.outbox,
        )

    def tearDown(self):
        self.temp_dir.cleanup()

    async def test_game_card_is_durably_queued_before_source_batch_is_removed(self):
        source_dir = make_batch(self.root)

        await self.watcher._tick()

        self.assertFalse(source_dir.exists())
        pending = self.outbox.list_pending()
        self.assertEqual(len(pending), 1)
        self.assertEqual(pending[0].kind, "image")
        self.assertEqual(pending[0].recipient, "g1")
        self.assertEqual(pending[0].text, "【星落】v1-A")
        self.assertEqual(
            (self.outbox.data_dir / pending[0].media_path).read_bytes(),
            b"character-card",
        )

    async def test_reprocessing_batch_is_idempotent_after_queue_commit(self):
        source_dir = make_batch(self.root)
        batch = list_version_batches(self.config.watch.outbox_dir)[0]

        await self.watcher._process_batch(batch)
        # Simulate restart before a producer-side completion marker can be
        # written: replaying the same stable key must not duplicate the row.
        source_dir.mkdir()
        image = source_dir / "1_A_角色档案_长图.png"
        image.write_bytes(b"character-card")
        (source_dir / "manifest.json").write_text(
            json.dumps(
                {
                    "version": "v1",
                    "characters": [{"id": "1", "name": "A", "file_name": image.name}],
                }
            ),
            encoding="utf-8",
        )
        await self.watcher._process_batch(
            list_version_batches(self.config.watch.outbox_dir)[0]
        )

        self.assertEqual(self.outbox.count(), 1)

    async def test_game_lifecycle_messages_are_queued_in_order_with_card(self):
        make_batch(self.root, "100")
        (self.root / "update_events.jsonl").write_text(
            "\n".join(
                (
                    json.dumps({"event": "start", "version": 100}),
                    json.dumps(
                        {"event": "finish", "version": 100, "new_characters": 1}
                    ),
                )
            )
            + "\n",
            encoding="utf-8",
        )

        await self.watcher._tick()

        pending = self.outbox.list_pending()
        self.assertEqual([item.kind for item in pending], ["text", "image", "text"])
        self.assertEqual(
            [item.sequence for item in pending],
            sorted(item.sequence for item in pending),
        )
        self.assertEqual(pending[0].text, "检测到新版本，正在自动更新，期间将暂停服务")
        self.assertEqual(pending[1].text, "【星落】100-A")
        self.assertEqual(pending[2].text, "版本更新结束，一共有1个新角色")
        self.assertEqual(pending[0].event_key, "game-update:1:start:100")
        self.assertEqual(pending[2].event_key, "game-update:2:finish:100")
        self.assertEqual(self.watcher.notice.consumed_events, 2)

    async def test_multiple_update_lifecycles_keep_each_start_before_its_cards(self):
        make_batch(self.root, "100")
        make_batch(self.root, "200")
        events = [
            {"event": "start", "version": 100},
            {"event": "finish", "version": 100, "new_characters": 1},
            {"event": "start", "version": 200},
            {"event": "finish", "version": 200, "new_characters": 1},
        ]
        (self.root / "update_events.jsonl").write_text(
            "\n".join(json.dumps(event) for event in events) + "\n",
            encoding="utf-8",
        )

        await self.watcher._tick()

        pending = self.outbox.list_pending()
        self.assertEqual(
            [item.kind for item in pending],
            ["text", "image", "text", "text", "image", "text"],
        )
        self.assertEqual(
            [item.event_key for item in pending],
            [
                "game-update:1:start:100",
                "game-card:100:1:1_A_角色档案_长图.png",
                "game-update:2:finish:100",
                "game-update:3:start:200",
                "game-card:200:1:1_A_角色档案_长图.png",
                "game-update:4:finish:200",
            ],
        )

    async def test_enqueue_failure_keeps_source_and_does_not_advance_notice_offset(
        self,
    ):
        source_dir = make_batch(self.root)
        (self.root / "update_events.jsonl").write_text(
            json.dumps({"event": "start", "version": 100}) + "\n",
            encoding="utf-8",
        )
        original_enqueue = self.outbox.enqueue_image

        def fail_enqueue(*args, **kwargs):
            raise OSError("simulated SQLite failure")

        self.outbox.enqueue_image = fail_enqueue

        with self.assertRaises(OSError):
            await self.watcher._tick()

        self.outbox.enqueue_image = original_enqueue
        self.assertTrue(source_dir.exists())
        self.assertEqual(self.watcher.notice.consumed_events, 1)
        self.assertEqual(self.outbox.count(), 1)  # start notice already committed


if __name__ == "__main__":
    unittest.main()
