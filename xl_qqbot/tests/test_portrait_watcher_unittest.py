"""portrait_ready 事件逐套送达 watcher 测试。"""

import hashlib
import json
import tempfile
import unittest
from pathlib import Path

from bot_app.config import (
    BotConfig,
    Config,
    MessageConfig,
    TargetConfig,
    UploadConfig,
    WatchConfig,
)
from bot_app.proactive_outbox import ProactiveOutbox
from bot_app.watcher import Watcher

PNG_MAGIC = b"\x89PNG\r\n\x1a\n"


def png_bytes(tag: str) -> bytes:
    return PNG_MAGIC + tag.encode("utf-8")


def make_config(root: Path, groups, *, file_base_url: str = "") -> Config:
    return Config(
        bot=BotConfig(appid="1", secret="2"),
        watch=WatchConfig(
            outbox_dir=str(root / "source-outbox"),
            interval_seconds=30,
            data_dir=str(root / "data"),
        ),
        target=TargetConfig(group_openids=list(groups), auto_learn_from_events=False),
        upload=UploadConfig(file_base_url=file_base_url),
        message=MessageConfig(template="【星落】{version}-{name}"),
    )


class PortraitWatcherTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.root = Path(self.temp_dir.name)
        self.outbox_dir = self.root / "source-outbox"
        self.outbox_dir.mkdir()
        self.events_path = self.root / "update_events.jsonl"
        self.portrait_outbox = self.root / "portrait_outbox"
        self.config = make_config(self.root, ["group-a"])
        self.outbox = ProactiveOutbox(self.root / "data")
        self.watcher = Watcher(self.config, None, proactive_outbox=self.outbox)

    def tearDown(self):
        self.temp_dir.cleanup()

    # ---- 事件构造 -----------------------------------------------------------

    def _write_portrait(self, version: str, character_id: str, skin_id: str, body: bytes) -> dict:
        sha256 = hashlib.sha256(body).hexdigest()
        file_name = f"{character_id}_{skin_id}_{sha256[:12]}.png"
        version_dir = self.portrait_outbox / version
        version_dir.mkdir(parents=True, exist_ok=True)
        (version_dir / file_name).write_bytes(body)
        return {
            "event": "portrait_ready",
            "version": version,
            "character_id": character_id,
            "skin_id": skin_id,
            "ordinal": 1,
            "name": "雾铃",
            "path": f"portrait_outbox/{version}/{file_name}",
            "sha256": sha256,
            "width": 2903,
            "height": 2761,
            "at": "2026-11-08T10:00:00+08:00",
        }

    def _write_events(self, payloads) -> None:
        self.events_path.write_text(
            "".join(json.dumps(item, ensure_ascii=False) + "\n" for item in payloads),
            encoding="utf-8",
        )

    def _lifecycle(self, version="134381674183448664", **finish_extra):
        finish = {
            "event": "finish",
            "version": int(version),
            "new_characters": 2,
            "error": None,
            "at": "2026-11-08T10:06:00+08:00",
        }
        finish.update(finish_extra)
        return [
            {"event": "start", "version": int(version), "at": "2026-11-08T09:59:00+08:00"},
            finish,
        ]

    # ---- 逐套顺序与多群 -----------------------------------------------------

    async def test_portraits_are_queued_in_event_order_before_finish(self):
        version = "134381674183448664"
        first = self._write_portrait(version, "10000224", "1000022401", png_bytes("one"))
        second = self._write_portrait(version, "10000224", "1000022402", png_bytes("two"))
        events = self._lifecycle(version)
        events[1:1] = [first, second]
        self._write_events(events)

        await self.watcher._tick()

        pending = self.outbox.list_pending()
        self.assertEqual(
            [(item.kind, item.text) for item in pending],
            [
                ("text", "检测到新版本，正在自动更新，期间将暂停服务"),
                ("image", "【星落】雾铃 立绘（1）"),
                ("image", "【星落】雾铃 立绘（1）"),
                ("text", "版本更新结束，一共有2个新角色"),
            ],
        )
        self.assertEqual(self.watcher.notice.consumed_events, 4)
        images = [item for item in pending if item.kind == "image"]
        self.assertTrue(all(item.media_path for item in images))
        for item in images:
            spool = self.outbox.data_dir / item.media_path
            self.assertTrue(spool.is_file())
            self.assertEqual(spool.read_bytes(), PNG_MAGIC + (b"one" if item is images[0] else b"two"))

    async def test_portrait_enqueued_for_every_target_group(self):
        config = make_config(self.root, ["group-a", "group-b"])
        watcher = Watcher(config, None, proactive_outbox=self.outbox)
        version = "134381674183448664"
        ready = self._write_portrait(version, "10000224", "1000022402", png_bytes("one"))
        events = self._lifecycle(version)
        events[1:1] = [ready]
        self._write_events(events)

        await watcher._tick()

        images = [
            item for item in self.outbox.list_pending() if item.kind == "image"
        ]
        self.assertEqual(
            sorted((item.recipient, item.ordinal) for item in images),
            [("group-a", 0), ("group-b", 0)],
        )

    async def test_portrait_event_key_contains_source_version_character_skin_sha(self):
        version = "134381674183448664"
        body = png_bytes("one")
        ready = self._write_portrait(version, "10000224", "1000022402", body)
        events = self._lifecycle(version)
        events[1:1] = [ready]
        self._write_events(events)

        await self.watcher._tick()

        expected_sha = hashlib.sha256(body).hexdigest()[:12]
        rows = [
            item
            for item in self.outbox.list_pending()
            if item.kind == "image"
        ]
        self.assertEqual(
            {item.event_key for item in rows},
            {f"game-portrait:{version}:10000224:1000022402:{expected_sha}"},
        )

    # ---- 幂等与崩溃恢复 -----------------------------------------------------

    async def test_crash_between_enqueue_and_cursor_redelivers_idempotently(self):
        version = "134381674183448664"
        ready = self._write_portrait(version, "10000224", "1000022402", png_bytes("one"))
        events = self._lifecycle(version)
        events[1:1] = [ready]
        self._write_events(events)

        original = self.watcher.notice.mark_consumed

        def flaky_mark(count: int) -> None:
            if count == 2:  # portrait_ready 行
                raise RuntimeError("simulated crash after enqueue")
            original(count)

        self.watcher.notice.mark_consumed = flaky_mark
        with self.assertRaises(RuntimeError):
            await self.watcher._tick()
        self.assertEqual(self.watcher.notice.consumed_events, 1)

        self.watcher.notice.mark_consumed = original
        await self.watcher._tick()

        rows = [item for item in self.outbox.list_pending() if item.kind == "image"]
        self.assertEqual(len(rows), 1)
        self.assertEqual(self.watcher.notice.consumed_events, 3)

    async def test_missing_attachment_does_not_advance_cursor(self):
        version = "134381674183448664"
        ready = self._write_portrait(version, "10000224", "1000022402", png_bytes("one"))
        # 事件指向不存在的文件
        (self.portrait_outbox / version / Path(ready["path"]).name).unlink()
        events = self._lifecycle(version)
        events[1:1] = [ready]
        self._write_events(events)

        with self.assertRaises(FileNotFoundError):
            await self.watcher._tick()

        self.assertEqual(self.watcher.notice.consumed_events, 1)
        self.assertEqual(
            [item for item in self.outbox.list_pending() if item.kind == "image"], []
        )

    async def test_sha_mismatch_does_not_advance_cursor(self):
        version = "134381674183448664"
        ready = self._write_portrait(version, "10000224", "1000022402", png_bytes("one"))
        ready["sha256"] = "ff" * 32
        events = self._lifecycle(version)
        events[1:1] = [ready]
        self._write_events(events)

        with self.assertRaises(ValueError):
            await self.watcher._tick()

        self.assertEqual(self.watcher.notice.consumed_events, 1)
        self.assertEqual(
            [item for item in self.outbox.list_pending() if item.kind == "image"], []
        )

    async def test_non_png_attachment_does_not_advance_cursor(self):
        version = "134381674183448664"
        body = b"not-a-png-file"
        sha256 = hashlib.sha256(body).hexdigest()
        file_name = f"10000224_1000022402_{sha256[:12]}.png"
        version_dir = self.portrait_outbox / version
        version_dir.mkdir(parents=True, exist_ok=True)
        (version_dir / file_name).write_bytes(body)
        ready = {
            "event": "portrait_ready",
            "version": version,
            "character_id": "10000224",
            "skin_id": "1000022402",
            "ordinal": 1,
            "name": "雾铃",
            "path": f"portrait_outbox/{version}/{file_name}",
            "sha256": sha256,
            "width": 1,
            "height": 1,
            "at": "2026-11-08T10:00:00+08:00",
        }
        events = self._lifecycle(version)
        events[1:1] = [ready]
        self._write_events(events)

        with self.assertRaises(ValueError):
            await self.watcher._tick()

        self.assertEqual(self.watcher.notice.consumed_events, 1)
        self.assertEqual(
            [item for item in self.outbox.list_pending() if item.kind == "image"], []
        )

    async def test_path_traversal_is_rejected_without_advancing_cursor(self):
        version = "134381674183448664"
        ready = self._write_portrait(version, "10000224", "1000022402", png_bytes("one"))
        ready["path"] = f"../outbox/evil_{hashlib.sha256(png_bytes('one')).hexdigest()[:12]}.png"
        events = self._lifecycle(version)
        events[1:1] = [ready]
        self._write_events(events)

        with self.assertRaises(ValueError):
            await self.watcher._tick()

        self.assertEqual(self.watcher.notice.consumed_events, 1)
        self.assertEqual(
            [item for item in self.outbox.list_pending() if item.kind == "image"], []
        )

    # ---- file_base_url 下仍强制 multipart -----------------------------------

    async def test_portrait_uses_local_multipart_even_with_file_base_url(self):
        config = make_config(
            self.root, ["group-a"], file_base_url="https://files.example.com/outbox"
        )
        watcher = Watcher(config, None, proactive_outbox=self.outbox)
        version = "134381674183448664"
        ready = self._write_portrait(version, "10000224", "1000022402", png_bytes("one"))
        events = self._lifecycle(version)
        events[1:1] = [ready]
        self._write_events(events)

        await watcher._tick()

        rows = [item for item in self.outbox.list_pending() if item.kind == "image"]
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0].image_delivery_mode, "local_multipart")
        self.assertTrue((self.outbox.data_dir / rows[0].media_path).is_file())

    # ---- finish 汇总与关闭时无变化 ------------------------------------------

    async def test_finish_appends_portrait_summary_when_present(self):
        version = "134381674183448664"
        ready = self._write_portrait(version, "10000224", "1000022402", png_bytes("one"))
        events = self._lifecycle(
            version, portraits={"success": 2, "failed": 1, "skipped": 0}
        )
        events[1:1] = [ready]
        self._write_events(events)

        await self.watcher._tick()

        texts = [item.text for item in self.outbox.list_pending() if item.kind == "text"]
        self.assertIn("版本更新结束，一共有2个新角色", texts[-1])
        self.assertIn("成功2", texts[-1])
        self.assertIn("失败1", texts[-1])
        self.assertIn("跳过0", texts[-1])

    async def test_finish_text_unchanged_without_portraits(self):
        version = "134381674183448664"
        ready = self._write_portrait(version, "10000224", "1000022402", png_bytes("one"))
        events = self._lifecycle(version)
        events[1:1] = [ready]
        self._write_events(events)

        await self.watcher._tick()

        texts = [item.text for item in self.outbox.list_pending() if item.kind == "text"]
        self.assertEqual(texts[-1], "版本更新结束，一共有2个新角色")

    async def test_no_portrait_events_keeps_legacy_behavior(self):
        self._write_events(self._lifecycle())

        await self.watcher._tick()

        pending = self.outbox.list_pending()
        self.assertEqual(
            [(item.kind, item.text) for item in pending],
            [
                ("text", "检测到新版本，正在自动更新，期间将暂停服务"),
                ("text", "版本更新结束，一共有2个新角色"),
            ],
        )
        self.assertFalse(self.watcher.mute.muted)
        self.assertEqual(self.watcher.notice.consumed_events, 2)

    # ---- 不与图鉴 batch 混淆 ------------------------------------------------

    async def test_portrait_is_not_treated_as_card_batch_and_file_survives(self):
        version = "134381674183448664"
        ready = self._write_portrait(version, "10000224", "1000022402", png_bytes("one"))
        card_dir = self.outbox_dir / version
        card_dir.mkdir()
        card_name = "10000224_雾铃_角色档案_长图.png"
        (card_dir / card_name).write_bytes(b"card")
        (card_dir / "manifest.json").write_text(
            json.dumps(
                {
                    "version": version,
                    "characters": [
                        {"id": "10000224", "name": "雾铃", "file_name": card_name}
                    ],
                },
                ensure_ascii=False,
            ),
            encoding="utf-8",
        )
        events = self._lifecycle(version)
        events[1:1] = [ready]
        self._write_events(events)

        await self.watcher._tick()

        # 图鉴 outbox 被清理（原有行为），立绘文件保留供后续查询
        self.assertFalse(card_dir.exists())
        portrait_file = self.portrait_outbox / version / Path(ready["path"]).name
        self.assertTrue(portrait_file.is_file())
        rows = self.outbox.list_pending()
        image_texts = sorted(item.text for item in rows if item.kind == "image")
        self.assertEqual(image_texts, sorted(["【星落】雾铃 立绘（1）", f"【星落】{version}-雾铃"]))
        # 图鉴卡片仍是 legacy 模式，立绘是 local_multipart
        modes = sorted(item.image_delivery_mode for item in rows if item.kind == "image")
        self.assertEqual(modes, ["legacy", "local_multipart"])

    # ---- notification_client 路径 -------------------------------------------

    async def test_portrait_enqueued_through_notification_client_with_mode(self):
        class FakeNotificationClient:
            def __init__(self):
                self.calls = []

            async def enqueue(self, *, event_key, instance_id, messages):
                self.calls.append({"event_key": event_key, "messages": messages})
                return [{"sequence": index} for index, _ in enumerate(messages)]

        client = FakeNotificationClient()
        config = make_config(self.root, ["group-a"])
        watcher = Watcher(
            config,
            sender=None,
            notification_client=client,
            proactive_outbox=None,
        )
        self.assertIsNone(watcher.proactive_outbox)
        version = "134381674183448664"
        body = png_bytes("one")
        ready = self._write_portrait(version, "10000224", "1000022402", body)
        events = self._lifecycle(version)
        events[1:1] = [ready]
        self._write_events(events)

        await watcher._tick()

        image_calls = [
            call
            for call in client.calls
            if any(message["kind"] == "image" for message in call["messages"])
        ]
        self.assertEqual(len(image_calls), 1)
        message = image_calls[0]["messages"][0]
        self.assertEqual(message["kind"], "image")
        self.assertEqual(message["image_delivery_mode"], "local_multipart")
        self.assertEqual(message["expected_sha256"], hashlib.sha256(body).hexdigest())
        self.assertEqual(
            Path(message["media_path"]),
            self.root / ready["path"],
        )
        expected_sha = hashlib.sha256(body).hexdigest()[:12]
        self.assertEqual(
            image_calls[0]["event_key"],
            f"game-portrait:{version}:10000224:1000022402:{expected_sha}",
        )
        self.assertEqual(watcher.notice.consumed_events, 3)


if __name__ == "__main__":
    unittest.main()
