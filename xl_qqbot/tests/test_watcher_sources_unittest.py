import json
import tempfile
import unittest
from pathlib import Path

from bot_app.config import (
    BotConfig,
    Config,
    MessageConfig,
    TargetConfig,
    UpdateSourceConfig,
    UploadConfig,
    WatchConfig,
)
from bot_app.proactive_outbox import ProactiveOutbox
from bot_app.tiers import GroupTier
from bot_app.config import GroupsConfig
from bot_app.watcher import Watcher


class NoDirectSender:
    async def send_text(self, *args, **kwargs):
        raise AssertionError("watcher must enqueue, never send directly")

    async def send_image(self, *args, **kwargs):
        raise AssertionError("watcher must enqueue, never send directly")


def make_config(root: Path, sources: tuple[UpdateSourceConfig, ...]) -> Config:
    main_outbox = root / "main" / "outbox"
    test_outbox = root / "test" / "outbox"
    main_outbox.mkdir(parents=True)
    test_outbox.mkdir(parents=True)
    return Config(
        bot=BotConfig(appid="app", secret="secret"),
        watch=WatchConfig(
            outbox_dir=str(main_outbox),
            interval_seconds=30,
            data_dir=str(root / "bot-data"),
            update_sources=sources,
        ),
        target=TargetConfig(group_openids=["prod", "test", "debug"], auto_learn_from_events=False),
        upload=UploadConfig(file_base_url=""),
        message=MessageConfig(template="{version}-{name}"),
        groups=GroupsConfig(test=["test"], debug=["debug"]),
    )


def make_card_batch(outbox_dir: Path, version: str) -> Path:
    version_dir = outbox_dir / version
    version_dir.mkdir()
    image_name = "10000224_雾铃_角色档案_长图.png"
    (version_dir / image_name).write_bytes(b"archive-card")
    (version_dir / "manifest.json").write_text(
        json.dumps(
            {
                "version": version,
                "characters": [
                    {"id": "10000224", "name": "雾铃", "file_name": image_name}
                ],
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    return version_dir


class WatcherSourceRoutingTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.main_dir = self.root / "main" / "outbox"
        self.test_dir = self.root / "test" / "outbox"
        self.config = make_config(
            self.root,
            (
                UpdateSourceConfig(name="main", outbox_dir=str(self.main_dir), minimum_tier="production"),
                UpdateSourceConfig(name="test", outbox_dir=str(self.test_dir), minimum_tier="test"),
            ),
        )
        self.outbox = ProactiveOutbox(self.config.watch.data_dir)
        self.tiers = GroupTier(self.config.groups)

    def tearDown(self):
        self.temp.cleanup()

    def _watcher(self, name):
        source = next(item for item in self.config.watch.update_sources if item.name == name)
        return Watcher(
            self.config,
            NoDirectSender(),
            tiers=self.tiers,
            proactive_outbox=self.outbox,
            source=source,
        )

    async def test_test_source_events_are_queued_only_to_test_and_debug_groups(self):
        (self.test_dir.parent / "update_events.jsonl").write_text(
            json.dumps({"event": "start", "version": 77}) + "\n",
            encoding="utf-8",
        )

        await self._watcher("test")._tick()

        pending = self.outbox.list_pending()
        self.assertEqual({item.recipient for item in pending}, {"test", "debug"})
        self.assertTrue(all(item.event_key.startswith("test:game-update:") for item in pending))

    async def test_main_source_keeps_legacy_event_key_and_production_reach(self):
        (self.main_dir.parent / "update_events.jsonl").write_text(
            json.dumps({"event": "start", "version": 77}) + "\n",
            encoding="utf-8",
        )

        await self._watcher("main")._tick()

        pending = self.outbox.list_pending()
        self.assertEqual({item.recipient for item in pending}, {"prod", "test", "debug"})
        self.assertTrue(all(item.event_key == "game-update:1:start:77" for item in pending))
        self.assertEqual(self._watcher("main").notice.consumed_events, 1)
        self.assertEqual(self._watcher("test").notice.consumed_events, 0)

    async def test_same_version_from_both_sources_does_not_collide_in_fifo(self):
        event = json.dumps({"event": "start", "version": 77}) + "\n"
        (self.main_dir.parent / "update_events.jsonl").write_text(event, encoding="utf-8")
        (self.test_dir.parent / "update_events.jsonl").write_text(event, encoding="utf-8")

        await self._watcher("main")._tick()
        await self._watcher("test")._tick()

        pending = self.outbox.list_pending()
        main_keys = {item.event_key for item in pending if item.recipient == "prod"}
        test_keys = {item.event_key for item in pending if item.recipient == "test"}
        self.assertEqual(main_keys, {"game-update:1:start:77"})
        self.assertEqual(
            test_keys,
            {"game-update:1:start:77", "test:game-update:1:start:77"},
        )
        self.assertEqual(
            len([item for item in pending if item.recipient == "test"]), 2
        )

    async def test_test_finish_queues_lifecycle_and_card_in_order(self):
        version_dir = make_card_batch(self.test_dir, "77")
        events = [
            {"event": "start", "version": 77},
            {"event": "finish", "version": 77, "new_characters": 1},
        ]
        (self.test_dir.parent / "update_events.jsonl").write_text(
            "\n".join(json.dumps(item) for item in events) + "\n",
            encoding="utf-8",
        )

        await self._watcher("test")._tick()

        self.assertFalse(version_dir.exists())
        pending = self.outbox.list_pending()
        for recipient in ("test", "debug"):
            recipient_items = [item for item in pending if item.recipient == recipient]
            self.assertEqual([item.kind for item in recipient_items], ["text", "image", "text"])
            self.assertEqual(
                [item.event_key for item in recipient_items],
                [
                    "test:game-update:1:start:77",
                    "test:game-card:77:10000224:10000224_雾铃_角色档案_长图.png",
                    "test:game-update:2:finish:77",
                ],
            )
        self.assertEqual(
            [item.recipient for item in pending if item.kind == "image"],
            ["debug", "test"],
        )


if __name__ == "__main__":
    unittest.main()
