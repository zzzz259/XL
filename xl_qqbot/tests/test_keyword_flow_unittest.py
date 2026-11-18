import asyncio
import hashlib
import io
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from PIL import Image

from bot_app.keyword_media import KeywordMedia, ReplyPart
from bot_app.keyword_store import KeywordStore
from bot_app.matcher import CharacterMatcher
from bot_app.querier import CharacterQuerier
from bot_app.query_handler import QueryHandler
from bot_app.selection import SelectionStore


class TestSender:
    __test__ = False

    def __init__(self):
        self.sent = []
        self.fail_image = False

    async def send_text(self, group, text, reply_to=""):
        self.sent.append(("text", group, text, reply_to))
        return True

    async def send_local_image(self, path, text, groups, reply_to=""):
        self.sent.append(("image", groups[0], Path(path).read_bytes(), reply_to))
        return {groups[0]: not self.fail_image}


class KeywordFlowTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.path = Path(temporary.name)
        self.character_data = self.path / "characters.json"
        self.character_data.write_text(
            json.dumps({"characters": {"1001": {"name": "菲欧/Feio", "star": 5}}}),
            encoding="utf-8",
        )
        self.store = KeywordStore(self.path)
        self.sender = TestSender()
        self.selection = SelectionStore()
        self.handler = QueryHandler(
            CharacterQuerier(self.character_data, self.path / "versions"),
            CharacterMatcher(self.character_data, self.path),
            self.selection, self.sender, bot_openid="thebot", keyword_store=self.store,
        )
        self.event_count = 0

    async def receive(
        self, text, *, group="group-a", member="alice", role="member", at=False,
        dedicated=False, message_id=None, attachment=None, **data_fields,
    ):
        self.event_count += 1
        message_id = message_id or f"message-{self.event_count}"
        mentions = [{"member_openid": "thebot", "is_you": True}] if at and not dedicated else []
        data = {
            "id": message_id,
            "group_openid": group,
            "content": ("<@thebot> " + text) if at else text,
            "author": {"member_openid": member, "member_role": role},
            "mentions": mentions,
            **data_fields,
        }
        if attachment:
            data["attachments"] = [attachment]
        await self.handler.handle({"event_type": "group_at" if dedicated else "group_message", "data": data})
        return message_id

    async def add(self, group, text, member="alice", name="Raid"):
        await self.receive(f"添加{name}", group=group, member=member, at=True)
        await self.receive(text, group=group, member=member)
        await self.receive("结束添加", group=group, member=member)

    async def test_full_group_messages_create_separate_same_name_entries(self):
        await self.receive("添加Raid", at=True)
        await self.receive("添加 raid ", group="group-b", at=True)
        await self.receive("A 一", message_id="duplicate")
        await self.receive("旁人的消息", member="bob")
        await self.receive("B 一", group="group-b")
        await self.receive("A 一", message_id="duplicate", dedicated=True, at=True)
        await self.receive("A 二")
        await self.receive("结束添加")
        await self.receive("结束添加", group="group-b")

        self.sender.sent.clear()
        query_a = await self.receive("rAiD", member="bob", at=True)
        query_b = await self.receive(" raid ", group="group-b", member="bob", at=True)
        self.assertEqual(self.sender.sent, [
            ("text", "group-a", "A 一\nA 二", query_a),
            ("text", "group-b", "B 一", query_b),
        ])

    async def test_trigger_redelivery_replies_once(self):
        await self.add("group-a", "正文")
        self.sender.sent.clear()
        await self.receive("Raid", at=True, message_id="replayed")
        await self.receive("Raid", at=True, dedicated=True, message_id="replayed")
        self.assertEqual(self.sender.sent, [("text", "group-a", "正文", "replayed")])

    async def test_delete_checks_group_and_sender_role(self):
        await self.add("group-a", "A")
        await self.add("group-b", "B")
        await self.receive("删除Raid", member="bob", at=True)
        self.assertIn("创建者或本群管理员", self.sender.sent[-1][2])
        self.assertIsNotNone(self.store.lookup("group-a", "Raid"))

        await self.receive("删除 Raid", member="bob", group="group-b", role="admin", at=True)
        self.assertIsNone(self.store.lookup("group-b", "Raid"))
        self.assertIsNotNone(self.store.lookup("group-a", "Raid"))

        await self.receive("删除Raid", member="alice", at=True, role="")
        self.assertIsNone(self.store.lookup("group-a", "Raid"))
        await self.receive("删除Raid", member="bob", at=True, role="owner")
        self.assertIn("本群没有这个关键词", self.sender.sent[-1][2])

    async def test_start_requires_real_mention_and_draft_control_is_scoped(self):
        await self.receive("星落罗伯特 添加Raid")
        await self.receive("添加Raid")
        await self.receive("<@someone> 添加Raid", mentions=[{"member_openid": "someone", "is_you": False}])
        self.assertIsNone(self.store.get_draft("group-a", "alice"))
        await self.receive("添加", at=True)
        self.assertIn("格式", self.sender.sent[-1][2])
        await self.receive("添加Raid", at=True, dedicated=True)
        await self.receive("结束添加", member="bob")
        await self.receive("结束添加")
        self.assertIn("继续录入", self.sender.sent[-1][2])
        await self.receive("添加另一个词", at=True)
        self.assertIn("当前", self.sender.sent[-1][2])
        await self.receive("取消添加")
        self.assertIsNone(self.store.get_draft("group-a", "alice"))
        self.assertIsNone(self.store.lookup("group-a", "Raid"))

    async def test_builtin_and_exact_character_names_are_reserved(self):
        for word in ("复刻表", "菲欧", "菲欧2", "菲欧-", "Feio", "1001", "结束添加"):
            with self.subTest(word=word):
                await self.receive(f"添加{word}", at=True)
                self.assertIn("冲突", self.sender.sent[-1][2])
                self.assertIsNone(self.store.get_draft("group-a", "alice"))

        await self.add("group-a", "自定义图文", name="开荒攻略2")
        await self.receive("开荒攻略2", at=True)
        self.assertEqual(self.sender.sent[-1][2], "自定义图文")
        await self.receive("菲欧", at=True)
        self.assertIn("菲欧", self.sender.sent[-1][2])

    async def test_regular_number_is_kept_as_body_for_recording_member(self):
        await self.receive("添加Raid", at=True)
        await self.receive("1")
        await self.receive("结束添加")
        await self.receive("Raid", at=True)
        self.assertEqual(self.sender.sent[-1][2], "1")

    async def test_plain_image_recording_keeps_order_and_stops_on_send_failure(self):
        await self.receive("添加Raid", at=True)
        await self.receive("图之前")
        photo = await self.receive_photo()
        await self.receive("图之后")
        await self.receive("结束添加")

        self.sender.sent.clear()
        query = await self.receive("Raid", member="bob", at=True)
        self.assertEqual(self.sender.sent, [
            ("text", "group-a", "图之前", query),
            ("image", "group-a", photo, query),
            ("text", "group-a", "图之后", query),
        ])

        self.sender.sent.clear()
        self.sender.fail_image = True
        await self.receive("Raid", member="bob", at=True)
        self.assertEqual([row[0] for row in self.sender.sent], ["text", "image", "text"])
        self.assertIn("失败", self.sender.sent[-1][2])

    async def test_finish_waits_for_preceding_image_event(self):
        entered = asyncio.Event()
        release = asyncio.Event()

        async def capture(media, draft_id, message_id, data, text):
            entered.set()
            await release.wait()
            return (ReplyPart("image", "", f"{draft_id}/photo.png", "fixture-digest"),)

        await self.receive("添加Raid", at=True)
        with patch.object(KeywordMedia, "capture", capture):
            image = asyncio.create_task(self.receive("", attachment={"url": "fixture"}))
            await asyncio.wait_for(entered.wait(), timeout=1)
            finish = asyncio.create_task(self.receive("结束添加"))
            await asyncio.sleep(0)
            self.assertFalse(finish.done())
            release.set()
            await asyncio.gather(image, finish)
        self.assertEqual(self.store.lookup("group-a", "Raid").parts[0]["kind"], "image")

    async def receive_photo(self):
        buffer = io.BytesIO()
        Image.new("RGB", (2, 2), "green").save(buffer, format="PNG")
        photo = buffer.getvalue()

        async def download(session, url, destination):
            destination.write_bytes(photo)
            return hashlib.sha256(photo).hexdigest()

        attachment = {"url": "https://multimedia.nt.qq.com.cn/download", "content_type": "image/png"}
        with patch("bot_app.keyword_media._download_image", download):
            await self.receive("", attachment=attachment)
        return photo

    async def test_delete_defers_media_cleanup_until_started_reply_finishes(self):
        await self.receive("添加Raid", at=True)
        photo = await self.receive_photo()
        await self.receive("结束添加")
        media_dir = self.path / "keyword_media" / self.store.lookup("group-a", "Raid").media_id
        image_started = asyncio.Event()
        release = asyncio.Event()
        send_image = self.sender.send_local_image

        async def blocked_image(path, text, groups, reply_to):
            image_started.set()
            await release.wait()
            return await send_image(path, text, groups, reply_to)

        self.sender.sent.clear()
        self.sender.send_local_image = blocked_image
        replay = asyncio.create_task(self.receive("Raid", member="bob", at=True))
        try:
            await asyncio.wait_for(image_started.wait(), timeout=1)
            await self.receive("删除Raid", member="mod", role="admin", at=True)
            self.assertIsNone(self.store.lookup("group-a", "Raid"))
            self.assertTrue(media_dir.is_dir())
        finally:
            release.set()
            reply_to = await replay
        self.assertIn(("image", "group-a", photo, reply_to), self.sender.sent)
        self.assertFalse(media_dir.exists())

    async def test_unaddressed_body_during_update_is_not_recorded(self):
        await self.receive("添加Raid", at=True)
        await self.handler.handle_event("group_message", {
            "id": "muted", "group_openid": "group-a", "content": "静音期间",
            "author": {"member_openid": "alice"},
        }, muted=True)
        await self.handler.handle_event("group_message", {}, muted=False)
        await self.receive("正常内容")
        await self.receive("结束添加")
        await self.receive("Raid", at=True)
        self.assertEqual(self.sender.sent[-1][2], "正常内容")


if __name__ == "__main__":
    unittest.main()
