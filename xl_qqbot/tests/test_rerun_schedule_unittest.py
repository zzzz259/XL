"""Standard-library tests for precise rerun intents and immutable image reads."""

import hashlib
import json
import tempfile
import unittest
from types import SimpleNamespace
from pathlib import Path
from unittest.mock import AsyncMock

from bot_app.query_handler import is_rerun_schedule_query
from bot_app.rerun_schedule_querier import RerunScheduleQuerier
from bot_app.sender import QQSender
from bot_app.config import GroupsConfig
from bot_app.query_handler import QueryHandler
from bot_app.tiers import GroupTier


PNG = b"\x89PNG\r\n\x1a\nnot-a-real-image-but-a-png-signature"


class RerunScheduleQueryTests(unittest.TestCase):
    def test_intent_matches_commands_and_specific_natural_questions_only(self):
        for query in ("复刻", "复刻表", "复刻排期", "下次复刻", "卡池复刻", "复刻时间", "下一期谁复刻", "罗蕾娜什么时候复刻？"):
            with self.subTest(query=query):
                self.assertTrue(is_rerun_schedule_query(query))
        self.assertFalse(is_rerun_schedule_query("我想看复刻活动公告"))
        self.assertFalse(is_rerun_schedule_query("复刻角色有哪些"))

    def test_querier_reads_versioned_image_and_reports_stale_snapshot(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            character_data = root / "character_data" / "current.json"
            character_data.parent.mkdir()
            character_data.write_text('{"characters":{}}', encoding="utf-8")
            (root / "current_version.json").write_text('{"version": 200}', encoding="utf-8")
            schedule = root / "versions" / "200" / "rerun_schedule"
            snapshot = schedule / "snapshots" / "200-abcd"
            snapshot.mkdir(parents=True)
            image = snapshot / "current.png"
            image.write_bytes(PNG)
            payload = {
                "source_version": "199",
                "snapshot_id": "200-abcd",
                "render_sha256": hashlib.sha256(PNG).hexdigest(),
                "artifact_path": "snapshots/200-abcd/current.png",
            }
            (schedule / "current.json").write_text(json.dumps(payload), encoding="utf-8")

            result = RerunScheduleQuerier(character_data, root / "versions").find()
            self.assertEqual(result.status, "found")
            self.assertTrue(result.stale)
            self.assertEqual(result.source_version, "199")
            self.assertEqual(result.image_path.read_bytes(), PNG)

    def test_querier_rejects_traversal_or_hash_mismatch(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            character_data = root / "character_data" / "current.json"
            character_data.parent.mkdir()
            character_data.write_text("{}", encoding="utf-8")
            (root / "current_version.json").write_text('{"version": 200}', encoding="utf-8")
            schedule = root / "versions" / "200" / "rerun_schedule"
            schedule.mkdir(parents=True)
            (schedule / "current.json").write_text(
                json.dumps({"artifact_path": "../../secret.png", "render_sha256": "x"}),
                encoding="utf-8",
            )
            result = RerunScheduleQuerier(character_data, root / "versions").find()
        self.assertEqual(result.status, "unavailable")

    def test_query_handler_sends_versioned_schedule_as_passive_image_reply(self):
        class Sender:
            def __init__(self):
                self.calls = []

            async def send_schedule_image(self, path, content, groups, reply_to):
                self.calls.append((path, content, groups, reply_to))
                return {groups[0]: True}

            async def send_text(self, group, text, reply_to=""):
                self.calls.append(("text", group, text, reply_to))
                return True

        class Querier:
            def find(self):
                return SimpleNamespace(
                    status="found", image_path=Path("/safe/current.png"),
                    source_version="123", stale=False, reason="",
                )

        async def run_query():
            sender = Sender()
            handler = QueryHandler(
                None, None, None, sender,
                tiers=GroupTier(GroupsConfig(debug=["g-debug"], features={"rerun_schedule_query": "debug"})),
                rerun_querier=Querier(),
            )
            await handler._answer_query("g-debug", "复刻表", reply_to="msg-1")
            return sender.calls

        import asyncio
        calls = asyncio.run(run_query())
        self.assertEqual(calls, [(str(Path("/safe/current.png")), "", ["g-debug"], "msg-1")])


class RerunScheduleUploadTests(unittest.IsolatedAsyncioTestCase):
    async def test_schedule_image_uses_local_multipart_not_outbox_url_mapping(self):
        with tempfile.TemporaryDirectory() as directory:
            image = Path(directory) / "current.png"
            image.write_bytes(PNG)
            sender = QQSender.__new__(QQSender)
            sender.config = SimpleNamespace(upload=SimpleNamespace(file_base_url="https://cdn.example/outbox"))
            sender._upload_by_multipart = AsyncMock(return_value={"file_info": "media"})
            sender.http = SimpleNamespace(request=AsyncMock(return_value={"file_info": "url-media"}))

            result = await sender._upload_group_file("g1", str(image), force_multipart=True)

        self.assertEqual(result["file_info"], "media")
        sender._upload_by_multipart.assert_awaited_once_with("g1", str(image))
        sender.http.request.assert_not_awaited()


if __name__ == "__main__":
    unittest.main()
