import importlib.util
import io
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch


class LeaderboardCLITests(unittest.TestCase):
    def module(self):
        self.assertIsNotNone(importlib.util.find_spec("leaderboard_cli"), "CLI is missing")
        import leaderboard_cli

        return leaderboard_cli

    def test_dry_run_never_loads_credentials_or_contacts_network(self):
        mod = self.module()
        output = io.StringIO()
        with patch.object(mod, "parse_credentials") as credentials:
            code = mod.run(["dry-run"], output=output)
        self.assertEqual(code, 0)
        credentials.assert_not_called()
        result = json.loads(output.getvalue())
        self.assertEqual(result["evidence"], "STATIC_CONFIRMED")
        self.assertFalse(result["network"])

    def test_first_error_stops_other_modes_and_does_not_print_exception_secret(self):
        mod = self.module()
        with tempfile.TemporaryDirectory() as tmp:
            output = io.StringIO()
            with (
                patch.object(mod, "parse_credentials", return_value=object()),
                patch.object(mod, "GameGatewaySession") as gateway,
                patch.object(mod, "collect_mode", side_effect=RuntimeError("PRIVATE_SECRET")) as collect,
            ):
                gateway.return_value.__enter__.return_value.requests_used = 1
                code = mod.run(["smoke", "--database", str(Path(tmp)/"rank.sqlite3"),
                                "--scope", "test-context"], input_stream=io.StringIO("private\n"), output=output)
            self.assertEqual(code, 2)
            self.assertEqual(collect.call_count, 1)
            self.assertNotIn("PRIVATE_SECRET", output.getvalue())
            result = json.loads(output.getvalue())
            self.assertEqual(result["archive"]["attempts"], 1)
            self.assertEqual(result["archive"]["snapshot_ids"], [])

    def test_login_failure_is_archived_without_trying_ranking_requests(self):
        mod = self.module()
        with tempfile.TemporaryDirectory() as tmp:
            output = io.StringIO()
            with (
                patch.object(mod, "parse_credentials", return_value=object()),
                patch.object(mod, "GameGatewaySession") as gateway,
                patch.object(mod, "collect_mode") as collect,
            ):
                gateway.return_value.__enter__.side_effect = mod.GachaError("AUTH_REJECTED")
                gateway.return_value.requests_used = 0
                code = mod.run(["smoke", "--database", str(Path(tmp)/"rank.sqlite3"),
                                "--scope", "test-context"], input_stream=io.StringIO("private\n"), output=output)
            self.assertEqual(code, 2)
            collect.assert_not_called()
            result = json.loads(output.getvalue())
            self.assertEqual(result["archive"]["attempts"], 1)
            self.assertEqual(result["results"][0]["collection_error"], "AUTH_REJECTED")

    def test_real_collector_archive_and_export_preserve_large_ids(self):
        mod = self.module()
        from test_leaderboards import FakeSession, actor, meta, page

        session = FakeSession(meta("illusion"), page("illusion", [actor("illusion")]))
        session.requests_used = 2
        with tempfile.TemporaryDirectory() as tmp:
            db = Path(tmp)/"rank.sqlite3"
            output = io.StringIO()
            with (
                patch.object(mod, "parse_credentials", return_value=object()),
                patch.object(mod, "GameGatewaySession") as gateway,
                patch("gacha_core.leaderboards.time.time", return_value=100),
            ):
                gateway.return_value.__enter__.return_value = session
                # collect_mode binds time.time at import; use its injectable clock.
                actual = mod.collect_mode
                with patch.object(mod, "collect_mode", side_effect=lambda *a, **kw: actual(*a, now=lambda:100, **kw)):
                    code = mod.run(["smoke", "--database", str(db), "--scope", "test-context",
                                    "--modes", "illusion"], input_stream=io.StringIO("private\n"), output=output)
            self.assertEqual(code, 3)  # first-page-only is partial evidence
            saved = json.loads(output.getvalue())["archive"]
            self.assertEqual(saved["attempts"], 1)
            self.assertEqual(len(saved["snapshot_ids"]), 1)
            dest = Path(tmp)/"ranking.json"
            self.assertEqual(mod.run(["export", "--database", str(db), "--snapshot-id",
                                      str(saved["snapshot_ids"][0]), "--output", str(dest)], output=io.StringIO()), 0)
            payload = json.loads(dest.read_text(encoding="utf-8"))
            self.assertEqual(payload["entries"][0]["entity_id"], "9007199254740993")

    def test_history_commands_never_construct_a_game_session(self):
        mod = self.module()
        with tempfile.TemporaryDirectory() as tmp:
            with patch.object(mod, "GameGatewaySession") as gateway:
                output = io.StringIO()
                self.assertEqual(mod.run(["list-seasons", "--database", str(Path(tmp)/"rank.sqlite3")], output=output), 0)
            gateway.assert_not_called()

    def test_metadata_command_archives_three_active_observations_without_snapshots(self):
        mod = self.module()
        from test_leaderboards import FakeSession, meta

        session = FakeSession(*(meta(mode) for mode in mod.MODES))
        session.requests_used = 3
        with tempfile.TemporaryDirectory() as tmp:
            output = io.StringIO()
            actual = mod.collect_mode
            with (
                patch.object(mod, 'parse_credentials', return_value=object()),
                patch.object(mod, 'GameGatewaySession') as gateway,
                patch.object(mod, 'collect_mode', side_effect=lambda *a, **kw:actual(*a, now=lambda:100, **kw)),
            ):
                gateway.return_value.__enter__.return_value = session
                code = mod.run(['metadata', '--database', str(Path(tmp)/'rank.sqlite3'),
                                '--scope', 'test-context'], input_stream=io.StringIO('private\n'), output=output)
            self.assertEqual(code, 0)
            result = json.loads(output.getvalue())
            self.assertEqual(result['archive']['attempts'], 3)
            self.assertEqual(result['archive']['snapshot_ids'], [])
            self.assertEqual([cmd for cmd, _ in session.calls], [2301, 2001, 3001])


if __name__ == "__main__":
    unittest.main()
