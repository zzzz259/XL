import tempfile
import unittest
from pathlib import Path

from bot_app.automation import build_automation_runtime
from bot_app.config import (
    BilibiliConfig,
    BotConfig,
    Config,
    GroupsConfig,
    MessageConfig,
    ServerStatusConfig,
    TargetConfig,
    UpdateSourceConfig,
    UploadConfig,
    WatchConfig,
)
from bot_app.status_monitor import StatusSample


class FakeNotificationClient:
    async def enqueue(self, **_request):
        return []


def make_config(root, *, status_enabled=False, bilibili_enabled=True):
    main_outbox = root / "main" / "outbox"
    test_outbox = root / "test" / "outbox"
    main_outbox.mkdir(parents=True)
    test_outbox.mkdir(parents=True)
    return Config(
        bot=BotConfig(appid="1", secret="2"),
        watch=WatchConfig(
            outbox_dir=str(main_outbox),
            interval_seconds=30,
            data_dir=str(root / "bot-data"),
            update_sources=(
                UpdateSourceConfig(name="main", outbox_dir=str(main_outbox)),
                UpdateSourceConfig(
                    name="test", outbox_dir=str(test_outbox), minimum_tier="test"
                ),
            ),
        ),
        target=TargetConfig(
            group_openids=["production-group", "test-group", "debug-group"],
            auto_learn_from_events=False,
        ),
        upload=UploadConfig(file_base_url=""),
        message=MessageConfig(template=""),
        groups=GroupsConfig(debug=["debug-group"], test=["test-group"]),
        bilibili=BilibiliConfig(enabled=bilibili_enabled),
        server_status=ServerStatusConfig(enabled=status_enabled),
    )


class AutomationRuntimeTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.root = Path(self.temp_dir.name)

    def tearDown(self):
        self.temp_dir.cleanup()

    def test_test_runtime_contains_test_updates_and_optional_status_only(self):
        config = make_config(self.root, status_enabled=True)
        runtime = build_automation_runtime(
            config,
            tier="test",
            notification_client=FakeNotificationClient(),
            probe_factory=lambda _config: lambda: StatusSample(True, 0, True, 1, False),
        )

        self.assertEqual([watcher.source.name for watcher in runtime.watchers], ["test"])
        self.assertIsNone(runtime.bilibili_watcher)
        self.assertIsNotNone(runtime.status_monitor)

    def test_production_runtime_contains_main_update_and_bilibili_only(self):
        config = make_config(self.root)
        runtime = build_automation_runtime(
            config, tier="production", notification_client=FakeNotificationClient()
        )

        self.assertEqual([watcher.source.name for watcher in runtime.watchers], ["main"])
        self.assertIsNotNone(runtime.bilibili_watcher)
        self.assertIsNone(runtime.status_monitor)

    def test_production_runtime_rejects_server_status_monitoring(self):
        config = make_config(self.root, status_enabled=True)

        with self.assertRaisesRegex(ValueError, "production.*server_status"):
            build_automation_runtime(
                config,
                tier="production",
                notification_client=FakeNotificationClient(),
            )


if __name__ == "__main__":
    unittest.main()
