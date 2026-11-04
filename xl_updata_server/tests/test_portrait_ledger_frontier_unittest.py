"""Append-authenticated read-only reconciliation frontier."""

import sqlite3
import tempfile
import unittest
from pathlib import Path

import test_portrait_runtime_ledger_unittest as fixtures
from server_app.portraits.runtime_ledger import RuntimeLedger


class FrontierTests(unittest.TestCase):
    def test_append_is_visible_without_full_history_restart_and_rollback_is_rejected(
        self,
    ):
        self.assertTrue(
            hasattr(RuntimeLedger, "read_changes_page"),
            "incremental authenticated frontier missing",
        )
        with tempfile.TemporaryDirectory() as directory:
            ledger = RuntimeLedger(
                Path(directory) / "ledger.db", "i", "s", enabled=True
            )
            with ledger.tick_owned():
                ledger.observe(
                    fixtures.CORE,
                    fixtures.observation(),
                    previous_cursor=None,
                    cursor=fixtures.cursor(),
                    now=100,
                )
            first = ledger.read_changes_page(limit=16)
            self.assertTrue(first["caught_up"])
            self.assertEqual(["a"], [row["run_id"] for row in first["items"]])
            external = RuntimeLedger(ledger.path, "i", "s", enabled=True)
            with external.tick_owned():
                external.start_source(fixtures.CORE, "a", now=101)
            second = ledger.read_changes_page(cursor=first["cursor"], limit=16)
            self.assertEqual("source_started", second["items"][0]["metadata_phase"])
            self.assertGreater(
                second["cursor"]["sequence"], first["cursor"]["sequence"]
            )
            self.assertEqual(
                [], ledger.read_changes_page(cursor=second["cursor"])["items"]
            )
            forged = dict(second["cursor"], prefix="f" * 64)
            with self.assertRaises(ValueError):
                ledger.read_changes_page(cursor=forged)
            with self.assertRaises(ValueError):
                ledger.read_changes_page(limit=17)
            with self.assertRaisesRegex(ValueError, "exceeds page bound"):
                ledger.read_changes_page(max_bytes=1)
            with sqlite3.connect(ledger.path) as db:
                db.execute(
                    "DELETE FROM history WHERE sequence>?",
                    (first["cursor"]["sequence"],),
                )
            with self.assertRaises(ValueError):
                ledger.read_changes_page(cursor=second["cursor"])

    def test_null_eligible_version_stays_rejected(self):
        with tempfile.TemporaryDirectory() as directory:
            ledger = RuntimeLedger(
                Path(directory) / "ledger.db", "i", "s", enabled=True
            )
            with ledger.tick_owned(), self.assertRaises(ValueError):
                ledger.observe(
                    fixtures.CORE,
                    dict(fixtures.observation(), version=None),
                    previous_cursor=None,
                    cursor=fixtures.cursor(),
                    now=100,
                )
            self.assertIsNone(ledger.read_cursor("outcomes"))


if __name__ == "__main__":
    unittest.main()
