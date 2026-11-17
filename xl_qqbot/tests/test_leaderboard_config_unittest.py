import tempfile
import unittest
from pathlib import Path

from bot_app.config import LeaderboardConfig, load_config

BASE = '[watch]\noutbox_dir="outbox"\n'


def write_config(extra=""):
    tmp = tempfile.TemporaryDirectory()
    path = Path(tmp.name) / "config.toml"
    path.write_text(BASE + extra, encoding="utf-8")
    return tmp, path


class LeaderboardConfigTests(unittest.TestCase):
    def test_leaderboard_is_disabled_by_default(self):
        tmp, path = write_config()
        self.addCleanup(tmp.cleanup)
        config = load_config(path)
        self.assertIsInstance(config.leaderboard, LeaderboardConfig)
        self.assertFalse(config.leaderboard.enabled)
        self.assertEqual(config.leaderboard.credentials_file, "")
        self.assertEqual(config.leaderboard.metadata_probe_interval, 600)
        self.assertEqual(config.leaderboard.request_delay, 2.0)
        self.assertEqual(config.leaderboard.ranking_max_requests, 80)
        self.assertEqual(config.leaderboard.ranking_max_seconds, 300)
        self.assertEqual(config.leaderboard.ranking_max_pages, 35)
        self.assertEqual(config.leaderboard.ranking_max_entries, 1000)
        self.assertEqual(config.leaderboard.lineup_max_requests, 400)
        self.assertEqual(config.leaderboard.lineup_max_seconds, 900)
        self.assertEqual(config.leaderboard.guild_index_max_requests, 200)
        self.assertEqual(config.leaderboard.guild_index_max_seconds, 600)

    def test_enabled_requires_credentials_scope_and_valid_shape(self):
        tmp, path = write_config(
            '[leaderboard]\nenabled=true\ncredentials_file="state.json"\n'
            'scope="cn:test-server:account-context"\n'
        )
        self.addCleanup(tmp.cleanup)
        config = load_config(path)
        self.assertTrue(config.leaderboard.enabled)
        self.assertEqual(config.leaderboard.scope, "cn:test-server:account-context")

        for extra in (
            '[leaderboard]\nenabled=true\nscope="x"\n',
            '[leaderboard]\nenabled=true\ncredentials_file="state.json"\n',
            '[leaderboard]\nenabled=true\ncredentials_file="state.json"\nscope="bad scope!"\n',
        ):
            tmp, path = write_config(extra)
            self.addCleanup(tmp.cleanup)
            with self.assertRaises(ValueError):
                load_config(path)

    def test_data_dir_defaults_under_watch_data_dir(self):
        tmp, path = write_config(
            'data_dir="./bot-data"\n'
            '[leaderboard]\nenabled=true\ncredentials_file="state.json"\n'
            'scope="cn:test"\n'
        )
        self.addCleanup(tmp.cleanup)
        config = load_config(path)
        self.assertEqual(Path(config.leaderboard.data_dir), Path("./bot-data") / "leaderboard")

    def test_probe_interval_request_delay_and_budgets_are_bounded(self):
        good = (
            '[leaderboard]\nenabled=true\ncredentials_file="state.json"\n'
            'scope="cn:test"\nmetadata_probe_interval=600\nrequest_delay=1.5\n'
            'ranking_max_requests=80\nranking_max_seconds=300\n'
            'lineup_max_requests=400\nlineup_max_seconds=900\n'
            'guild_index_max_requests=200\nguild_index_max_seconds=600\n'
        )
        tmp, path = write_config(good)
        self.addCleanup(tmp.cleanup)
        config = load_config(path)
        self.assertEqual(config.leaderboard.metadata_probe_interval, 600)
        self.assertEqual(config.leaderboard.request_delay, 1.5)

        over_budget = good.replace('ranking_max_requests=80', 'ranking_max_requests=201')
        tmp, path = write_config(over_budget)
        self.addCleanup(tmp.cleanup)
        with self.assertRaises(ValueError):
            load_config(path)

        over_seconds = good.replace('lineup_max_seconds=900', 'lineup_max_seconds=1801')
        tmp, path = write_config(over_seconds)
        self.addCleanup(tmp.cleanup)
        with self.assertRaises(ValueError):
            load_config(path)

        over_guild = good.replace('guild_index_max_requests=200', 'guild_index_max_requests=0')
        tmp, path = write_config(over_guild)
        self.addCleanup(tmp.cleanup)
        with self.assertRaises(ValueError):
            load_config(path)

        bad_delay = good.replace('request_delay=1.5', 'request_delay=0')
        tmp, path = write_config(bad_delay)
        self.addCleanup(tmp.cleanup)
        with self.assertRaises(ValueError):
            load_config(path)

        bad_interval = good.replace('metadata_probe_interval=600', 'metadata_probe_interval=10')
        tmp, path = write_config(bad_interval)
        self.addCleanup(tmp.cleanup)
        with self.assertRaises(ValueError):
            load_config(path)

    def test_ranking_max_pages_defaults_to_35_and_keeps_hard_cap(self):
        tmp, path = write_config(
            '[leaderboard]\nenabled=true\ncredentials_file="state.json"\nscope="cn:test"\n'
        )
        self.addCleanup(tmp.cleanup)
        self.assertEqual(load_config(path).leaderboard.ranking_max_pages, 35)

        at_cap = (
            '[leaderboard]\nenabled=true\ncredentials_file="state.json"\n'
            'scope="cn:test"\nranking_max_pages=60\n'
        )
        tmp, path = write_config(at_cap)
        self.addCleanup(tmp.cleanup)
        self.assertEqual(load_config(path).leaderboard.ranking_max_pages, 60)

        over_cap = at_cap.replace('ranking_max_pages=60', 'ranking_max_pages=61')
        tmp, path = write_config(over_cap)
        self.addCleanup(tmp.cleanup)
        with self.assertRaises(ValueError):
            load_config(path)

    def test_refresh_options_mirror_server_status_validation(self):
        good = (
            '[leaderboard]\nenabled=true\ncredentials_file="state.json"\n'
            'scope="cn:test"\nrefresh_enabled=true\nrefresh_proxy="direct"\n'
        )
        tmp, path = write_config(good)
        self.addCleanup(tmp.cleanup)
        self.assertTrue(load_config(path).leaderboard.refresh_enabled)

        tmp, path = write_config(good.replace('refresh_enabled=true', 'refresh_enabled="yes"'))
        self.addCleanup(tmp.cleanup)
        with self.assertRaises(ValueError):
            load_config(path)

        remote = good.replace('refresh_proxy="direct"', 'refresh_proxy="http://remote.invalid:7897"')
        tmp, path = write_config(remote)
        self.addCleanup(tmp.cleanup)
        with self.assertRaises(ValueError):
            load_config(path)


if __name__ == "__main__":
    unittest.main()
