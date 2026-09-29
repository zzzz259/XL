import tempfile
import unittest
from pathlib import Path

from bot_app.bilibili import BilibiliWatcher
from bot_app.config import (
    BilibiliConfig,
    BilibiliTarget,
    BotConfig,
    Config,
    GroupsConfig,
    MessageConfig,
    TargetConfig,
    UploadConfig,
    WatchConfig,
)
from bot_app.proactive_outbox import ProactiveOutbox


class NoDirectSender:
    async def send_text(self, *args, **kwargs):
        raise AssertionError("Bilibili proactive text must enter the router outbox")

    async def send_image(self, *args, **kwargs):
        raise AssertionError("Bilibili proactive images must enter the router outbox")


class PassThroughTiers:
    def filter_groups(self, _feature, groups):
        return list(groups)


class FakeClient:
    def __init__(self):
        self.opus_feed = [{"opus_id": 101, "content": "feed title"}]
        self.detail = {
            "type": 0,
            "id_str": "101",
            "modules": [
                {
                    "module_type": "MODULE_TYPE_TITLE",
                    "module_title": {"text": "full title"},
                },
                {
                    "module_type": "MODULE_TYPE_CONTENT",
                    "module_content": {
                        "paragraphs": [
                            {
                                "para_type": "2",
                                "pic": {"pics": [{"url": "https://example/p.jpg"}]},
                            }
                        ]
                    },
                },
            ],
        }
        self.downloads = []
        self.videos = [{"bvid": "BV1", "title": "PV", "created": 200}]

    async def fetch_opus_feed(self, _mid):
        return self.opus_feed

    async def fetch_opus_detail(self, _opus_id):
        return self.detail

    async def fetch_videos(self, _mid):
        return self.videos

    async def fetch_acc_info(self, _mid):
        return {"name": "UP"}

    async def download_image(self, _url, dest_dir, name="image", mid=None):
        path = Path(dest_dir) / f"{name}.jpg"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(b"bilibili-image")
        self.downloads.append(path)
        return str(path)


class BilibiliOutboxTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.root = Path(self.temp_dir.name)
        self.data_dir = self.root / "data"
        self.outbox = ProactiveOutbox(self.data_dir)
        self.config = Config(
            bot=BotConfig(appid="1", secret="2"),
            watch=WatchConfig(
                outbox_dir=str(self.root / "game-outbox"),
                interval_seconds=30,
                data_dir=str(self.data_dir),
            ),
            target=TargetConfig(
                group_openids=["g1", "g2"], auto_learn_from_events=False
            ),
            upload=UploadConfig(file_base_url=""),
            message=MessageConfig(template=""),
            bilibili=BilibiliConfig(
                enabled=True,
                targets=[BilibiliTarget(mid=123, name="UP", mode="notice")],
            ),
            groups=GroupsConfig(),
        )
        self.watcher = BilibiliWatcher(
            self.config,
            NoDirectSender(),
            tiers=PassThroughTiers(),
            proactive_outbox=self.outbox,
        )
        self.client = FakeClient()
        self.watcher._client = self.client
        self.state = self.watcher.state.target(123)
        self.state.mark_opus(100)

    def tearDown(self):
        self.temp_dir.cleanup()

    async def test_notice_opus_advances_cursor_only_after_all_groups_are_queued(self):
        await self.watcher._tick_opus(
            self.client, self.config.bilibili.targets[0], self.state
        )

        pending = self.outbox.list_pending()
        self.assertEqual([item.recipient for item in pending], ["g1", "g2"])
        self.assertEqual([item.kind for item in pending], ["text", "text"])
        self.assertEqual(self.state.last_opus_id, 101)
        self.assertEqual(
            [item.event_key for item in pending],
            ["bilibili:opus:123:101:notice", "bilibili:opus:123:101:notice"],
        )

    async def test_full_opus_queues_text_then_spooled_image_per_group(self):
        target = BilibiliTarget(mid=123, name="UP", mode="full")
        await self.watcher._tick_opus(self.client, target, self.state)

        pending = self.outbox.list_pending()
        self.assertEqual(
            [(item.kind, item.recipient) for item in pending],
            [("text", "g1"), ("text", "g2"), ("image", "g1"), ("image", "g2")],
        )
        self.assertEqual(self.state.last_opus_id, 101)
        self.assertEqual(pending[0].text, "【星落官方动态】full title")
        self.assertEqual(pending[2].ordinal, 0)
        self.assertEqual(
            (self.outbox.data_dir / pending[2].media_path).read_bytes(),
            b"bilibili-image",
        )
        self.assertFalse(self.client.downloads[0].exists())

    async def test_detail_or_image_enqueue_failure_does_not_advance_opus_cursor(self):
        target = BilibiliTarget(mid=123, name="UP", mode="full")
        original_enqueue = self.outbox.enqueue_image

        def fail_enqueue(*args, **kwargs):
            raise OSError("simulated database failure")

        self.outbox.enqueue_image = fail_enqueue
        with self.assertRaises(OSError):
            await self.watcher._tick_opus(self.client, target, self.state)
        self.outbox.enqueue_image = original_enqueue

        self.assertEqual(self.state.last_opus_id, 100)
        self.assertEqual(
            self.outbox.count(), 2
        )  # text records are durable and dedup on retry
        self.assertTrue(self.client.downloads[0].exists())

    async def test_video_high_water_advances_after_every_group_is_queued(self):
        target = self.config.bilibili.targets[0]
        self.state.mark_video(100, "BV0")
        self.client.videos = [
            {"bvid": "BV1", "title": "新 PV", "created": 200},
            {"bvid": "BV0", "title": "基线", "created": 100},
        ]

        await self.watcher._tick_videos(self.client, target, self.state)

        pending = self.outbox.list_pending()
        self.assertEqual([item.recipient for item in pending], ["g1", "g2"])
        self.assertEqual(self.state.last_bvid, "BV1")
        self.assertEqual(pending[0].event_key, "bilibili:video:123:BV1")


if __name__ == "__main__":
    unittest.main()
