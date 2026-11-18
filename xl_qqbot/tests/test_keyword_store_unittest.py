import tempfile
import threading
import unittest
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from bot_app.keyword_store import KeywordStore


def text_part(text):
    return {"kind": "text", "text": text, "media_path": "", "sha256": ""}


class KeywordStoreTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.store = KeywordStore(self.root)

    def draft(self, group="group-a", member="alice", name="Raid"):
        status, draft = self.store.start(group, member, name, 100)
        self.assertEqual(status, "started")
        self.assertIsNotNone(draft)
        return draft

    def add_entry(self, group, member, name, text):
        draft = self.draft(group, member, name)
        self.assertEqual(self.store.append(draft, "message-1", (text_part(text),), 102), "added")
        self.assertEqual(self.store.publish(draft, 103, tuple), "saved")
        return draft

    def test_same_keyword_is_unique_per_group(self):
        self.add_entry("group-a", "alice", " Raid ", "Alpha")
        self.add_entry("group-b", "alice", "RAID", "Bravo")

        self.assertEqual(self.store.start("group-a", "bob", "raid", 104), ("exists", None))
        self.assertEqual(self.store.lookup("group-a", "raid").parts[0]["text"], "Alpha")
        self.assertEqual(self.store.lookup("group-b", " rAid ").parts[0]["text"], "Bravo")
        self.assertIsNone(self.store.lookup("group-c", "raid"))

    def test_concurrent_publish_preserves_losing_draft_and_winner(self):
        left = self.draft()
        right = self.draft(member="bob", name="raid")
        self.store.append(left, "left", (text_part("Left"),), 110)
        self.store.append(right, "right", (text_part("Right"),), 110)
        barrier = threading.Barrier(2, timeout=5)

        def publish(draft):
            another_connection = KeywordStore(self.root)
            barrier.wait()
            return another_connection.publish(draft, 120, tuple)

        with ThreadPoolExecutor(max_workers=2) as workers:
            results = list(workers.map(publish, (left, right)))

        self.assertCountEqual(results, ["saved", "exists"])
        winner, loser = (left, right) if results[0] == "saved" else (right, left)
        self.assertEqual(self.store.lookup("group-a", "Raid").creator_openid, winner.member_openid)
        pending = self.store.get_draft("group-a", loser.member_openid)
        self.assertEqual(pending.draft_id, loser.draft_id)
        self.assertEqual(pending.expires_at, 710)

    def test_duplicate_body_and_restart_preserve_original_order(self):
        draft = self.draft()
        self.assertEqual(self.store.append(draft, "original-1", (text_part("文本一"),), 105), "added")
        self.assertEqual(self.store.append(draft, "original-2", (text_part("文本二"),), 110), "added")

        restarted = KeywordStore(self.root)
        current = restarted.get_draft("group-a", "alice")
        self.assertTrue(restarted.has_message(current, "original-1"))
        self.assertEqual(restarted.append(current, "original-1", (text_part("不同内容"),), 115), "duplicate")
        self.assertEqual(restarted.get_draft("group-a", "alice").expires_at, 710)
        self.assertEqual(restarted.publish(current, 116, tuple), "saved")
        self.assertIsNone(restarted.get_draft("group-a", "alice"))
        self.assertEqual(
            KeywordStore(self.root).lookup("group-a", "raid").parts,
            (text_part("文本一"), text_part("文本二")),
        )

    def test_each_member_has_one_draft_per_group(self):
        first = self.draft()
        self.assertEqual(self.store.start("group-a", "alice", "Another", 101), ("busy", None))
        distinct = self.draft("group-b", "alice", "Another")
        self.assertNotEqual(first.draft_id, distinct.draft_id)
        self.assertIsNone(self.store.get_draft("group-a", "bob"))

    def test_empty_draft_cancel_and_expiry(self):
        draft = self.draft()
        self.assertEqual(self.store.publish(draft, 120, tuple), "empty")
        self.assertIsNotNone(self.store.get_draft("group-a", "alice"))
        self.assertTrue(self.store.cancel(draft))
        self.assertIsNone(self.store.lookup("group-a", "Raid"))

        expired = self.draft()
        self.assertEqual(self.store.append(expired, "late", (text_part("late"),), 700), "inactive")
        self.assertEqual(self.store.expire(700), (expired.draft_id,))
        self.assertIsNone(self.store.get_draft("group-a", "alice"))

    def test_delete_uses_author_role_and_group(self):
        first = self.add_entry("group-a", "alice", "Raid", "Alpha")
        second = self.add_entry("group-b", "alice", "Raid", "Bravo")

        self.assertEqual(self.store.delete("group-a", "raid", "bob", "member"), ("forbidden", None))
        self.assertEqual(self.store.delete("group-a", "raid", "bob", ""), ("forbidden", None))
        self.assertEqual(self.store.delete("group-b", "raid", "bob", "admin"), ("deleted", second.draft_id))
        self.assertIsNotNone(self.store.lookup("group-a", "raid"))
        self.assertEqual(self.store.delete("group-a", "raid", "alice", ""), ("deleted", first.draft_id))
        self.assertEqual(self.store.delete("group-a", "raid", "alice", "owner"), ("missing", None))

    def test_instances_have_independent_database_files(self):
        self.add_entry("group-a", "alice", "Raid", "Alpha")
        separate = KeywordStore(self.root / "second-instance")
        self.assertIsNone(separate.lookup("group-a", "Raid"))


if __name__ == "__main__":
    unittest.main()
