"""Automation assembly of the optional leaderboard archive job."""
import asyncio
import base64
import json
import tempfile
import unittest
from pathlib import Path

from bot_app.automation import build_automation_runtime
from bot_app.config import (
    BilibiliConfig,
    BotConfig,
    Config,
    GroupsConfig,
    LeaderboardConfig,
    MessageConfig,
    ServerStatusConfig,
    TargetConfig,
    UpdateSourceConfig,
    UploadConfig,
    WatchConfig,
)
from bot_app.leaderboard_credentials import (
    claim_renewal_owner,
    release_renewal_owner,
    renewal_owner,
)
from bot_app.leaderboard_job import LeaderboardArchiveJob, build_leaderboard_job
from bot_app.renewing_probe import build_status_probe
from starfall_refresh.core import Store


def jwt():
    raw = json.dumps(
        {
            "iat": 100,
            "vfp": 300,
            "exp": 99999999999,
            "uid": "archive-owner",
            "appid": "2070035",
            "provider": "sms",
            "guid": "12345678-1234-1234-1234-123456789012",
        }
    )
    return "header." + base64.urlsafe_b64encode(raw.encode()).decode().rstrip("=") + ".fake"


def bundle():
    return {
        "version": 1,
        "profile": {
            "appkey": "synthetic",
            "signing_input": "synthetic",
            "params": {"appid": "2070035", "channel": "sms"},
        },
        "credential": {
            "open_id": "archive-owner",
            "token": jwt(),
            "channel": "sms",
        },
    }


class FakeNotificationClient:
    async def enqueue(self, **_request):
        return []


def make_config(root, *, leaderboard=None, server_status=None):
    main_outbox = root / "main" / "outbox"
    main_outbox.mkdir(parents=True, exist_ok=True)
    return Config(
        bot=BotConfig(appid="1", secret="2"),
        watch=WatchConfig(
            outbox_dir=str(main_outbox),
            interval_seconds=30,
            data_dir=str(root / "bot-data"),
            update_sources=(UpdateSourceConfig(name="main", outbox_dir=str(main_outbox)),),
        ),
        target=TargetConfig(group_openids=["group-a"], auto_learn_from_events=False),
        upload=UploadConfig(file_base_url=""),
        message=MessageConfig(template=""),
        groups=GroupsConfig(),
        bilibili=BilibiliConfig(enabled=False),
        server_status=server_status or ServerStatusConfig(),
        leaderboard=leaderboard or LeaderboardConfig(),
    )


def enabled_leaderboard(root, credentials_file, *, refresh_enabled=False):
    return LeaderboardConfig(
        enabled=True,
        credentials_file=str(credentials_file),
        refresh_enabled=refresh_enabled,
        refresh_proxy="direct",
        scope="cn:test-server:account-context",
        data_dir=str(root / "leaderboard-data"),
        metadata_probe_interval=600,
    )


class AssemblyTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)

    def test_runtime_has_no_leaderboard_job_by_default(self):
        config = make_config(self.root)
        runtime = build_automation_runtime(
            config,
            instance_id="jobs-a",
            notification_client=FakeNotificationClient(),
        )
        self.assertIsNone(runtime.leaderboard_job)

    def test_build_leaderboard_job_is_none_unless_enabled(self):
        config = make_config(self.root)
        self.assertIsNone(build_leaderboard_job(config))

    def test_enabled_runtime_assembles_job_and_stop_is_graceful(self):
        from tests.test_leaderboard_job_unittest import (
            Clock,
            FakeSessionFactory,
            World,
            guild_meta_pb,
        )

        world = World()
        world.guild_meta = guild_meta_pb(27, 2, 5)  # CLOSED: probes only, no slots
        clock = Clock()
        credentials = self.root / "credentials.json"
        credentials.write_text(json.dumps(
            {"open_id": "archive-owner", "token": jwt(), "channel": "sms"}),
            encoding="utf-8")
        import os
        if os.name == "posix":
            credentials.chmod(0o600)
        config = make_config(
            self.root, leaderboard=enabled_leaderboard(self.root, credentials))

        job = build_leaderboard_job(
            config,
            provider=None,
            session_factory=FakeSessionFactory(world),
            packager=None,
            now=clock.now,
            random_int=lambda a, b: 45,
            sleep=clock.sleep,
        )
        self.assertIsInstance(job, LeaderboardArchiveJob)
        self.assertFalse(job._provider._renewal)

        runtime = build_automation_runtime(
            config,
            instance_id="jobs-b",
            notification_client=FakeNotificationClient(),
            leaderboard_factory=lambda _config: job,
        )
        self.assertIs(runtime.leaderboard_job, job)
        asyncio.run(self._run_and_stop(runtime))

    async def _run_and_stop(self, runtime):
        task = asyncio.ensure_future(runtime.run())
        await asyncio.sleep(0.05)
        self.assertFalse(task.done())
        runtime.stop()
        await asyncio.wait_for(task, timeout=10)
        self.assertTrue(runtime.stop_event.is_set())

    def test_independent_state_path_makes_the_job_renewal_owner(self):
        state = self.root / "private" / "state.json"
        Store(state).initialize(bundle())
        status = ServerStatusConfig(
            enabled=True, credentials_file=str(self.root / "other-state.json"),
            refresh_enabled=True, refresh_proxy="direct")
        config = make_config(
            self.root,
            server_status=status,
            leaderboard=enabled_leaderboard(self.root, state, refresh_enabled=True),
        )
        self.addCleanup(release_renewal_owner, str(state), "leaderboard-job")
        job = build_leaderboard_job(config)
        self.assertFalse(job._provider._read_only)
        self.assertEqual(renewal_owner(state), "leaderboard-job")

    def test_same_state_path_as_status_monitor_binds_read_only(self):
        state = self.root / "private" / "state.json"
        Store(state).initialize(bundle())
        status = ServerStatusConfig(
            enabled=True, credentials_file=str(state),
            refresh_enabled=True, refresh_proxy="direct")
        config = make_config(
            self.root,
            server_status=status,
            leaderboard=enabled_leaderboard(self.root, state, refresh_enabled=True),
        )
        job = build_leaderboard_job(config)
        self.assertTrue(job._provider._read_only)
        self.assertIsNone(renewal_owner(state))

    def test_owner_conflict_falls_back_to_read_only(self):
        state = self.root / "private" / "state.json"
        Store(state).initialize(bundle())
        self.assertTrue(claim_renewal_owner(str(state), "someone-else"))
        self.addCleanup(release_renewal_owner, str(state), "someone-else")
        config = make_config(
            self.root,
            leaderboard=enabled_leaderboard(self.root, state, refresh_enabled=True),
        )
        job = build_leaderboard_job(config)
        self.assertTrue(job._provider._read_only)

    async def test_status_probe_waits_for_the_shared_account_lock(self):
        state = self.root / "private" / "state.json"
        Store(state).initialize(bundle())
        config = ServerStatusConfig(
            enabled=True, credentials_file=str(state),
            refresh_enabled=True, refresh_proxy="direct")
        from unittest.mock import Mock, patch

        client = Mock(return_value={"code": 0, "jwt": jwt()})
        client.gateway.return_value = "REACHABLE"
        from bot_app.leaderboard_credentials import shared_account_lock

        lock = shared_account_lock(str(state))
        with patch("bot_app.renewing_probe.HTTP", return_value=client):
            probe = build_status_probe(config, now=lambda: 500, account_lock=lock)
            async with lock:
                task = asyncio.ensure_future(probe())
                await asyncio.sleep(0.05)
                self.assertFalse(client.called,
                                 "probe must wait for the shared account lock")
            await asyncio.wait_for(task, timeout=10)
        self.assertTrue(client.called)


if __name__ == "__main__":
    unittest.main()
