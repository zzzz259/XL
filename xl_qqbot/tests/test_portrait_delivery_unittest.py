"""本地立绘发送别名测试：send_local_image 必须强制 multipart，不走公网 URL。"""

import tempfile
import unittest
from pathlib import Path
from unittest import mock

from bot_app.config import (
    BotConfig,
    Config,
    MessageConfig,
    TargetConfig,
    UploadConfig,
    WatchConfig,
)
from bot_app.gateway import GatewayTransportClient
from bot_app.sender import QQSender


def make_config(file_base_url: str = "https://files.example.com/outbox") -> Config:
    return Config(
        bot=BotConfig(appid="1", secret="2"),
        watch=WatchConfig(
            outbox_dir="/tmp/xl/outbox",
            interval_seconds=30,
            data_dir="/tmp/xl/data",
        ),
        target=TargetConfig(group_openids=["group-a"], auto_learn_from_events=False),
        upload=UploadConfig(file_base_url=file_base_url),
        message=MessageConfig(template=""),
    )


class SenderLocalImageTests(unittest.IsolatedAsyncioTestCase):
    async def test_send_local_image_forces_multipart_despite_file_base_url(self):
        config = make_config()
        sender = QQSender(config)
        captured = []

        async def fake_upload(group_openid, file_path, force_multipart=False):
            captured.append(force_multipart)
            return {"file_info": "info"}

        async def fake_send(group_openid, content, media, reply_to):
            return None

        with mock.patch.object(
            sender, "_upload_group_file", side_effect=fake_upload
        ), mock.patch.object(
            sender, "_send_group_media", side_effect=fake_send
        ):
            result = await sender.send_local_image(
                "/any/local/portrait.png", "caption", ["group-a"]
            )

        self.assertEqual(result, {"group-a": True})
        self.assertEqual(captured, [True])

    async def test_send_image_still_uses_public_url_when_configured(self):
        config = make_config()
        sender = QQSender(config)
        captured = []

        async def fake_upload(group_openid, file_path, force_multipart=False):
            captured.append(force_multipart)
            return {"file_info": "info"}

        async def fake_send(group_openid, content, media, reply_to):
            return None

        with mock.patch.object(
            sender, "_upload_group_file", side_effect=fake_upload
        ), mock.patch.object(
            sender, "_send_group_media", side_effect=fake_send
        ):
            result = await sender.send_image(
                "/any/local/card.png", "caption", ["group-a"]
            )

        self.assertEqual(result, {"group-a": True})
        self.assertEqual(captured, [False])


class GatewayLocalImageTests(unittest.IsolatedAsyncioTestCase):
    async def test_send_local_image_sets_force_multipart(self):
        client = GatewayTransportClient(
            "http://127.0.0.1:8785",
            bearer_token="t" * 40,
            instance_id="test",
        )
        payloads = []

        async def fake_send(payload):
            payloads.append(payload)
            return True

        with tempfile.TemporaryDirectory() as temp_dir:
            media = Path(temp_dir) / "portrait.png"
            media.write_bytes(b"png-bytes")
            with mock.patch.object(client, "_send", side_effect=fake_send):
                result = await client.send_local_image(
                    str(media), "caption", ["group-a"]
                )

        self.assertEqual(result, {"group-a": True})
        self.assertEqual(len(payloads), 1)
        self.assertIs(payloads[0]["force_multipart"], True)
        self.assertEqual(payloads[0]["kind"], "image")

    async def test_send_image_keeps_legacy_multipart_flag(self):
        client = GatewayTransportClient(
            "http://127.0.0.1:8785",
            bearer_token="t" * 40,
            instance_id="test",
        )
        payloads = []

        async def fake_send(payload):
            payloads.append(payload)
            return True

        with tempfile.TemporaryDirectory() as temp_dir:
            media = Path(temp_dir) / "card.png"
            media.write_bytes(b"png-bytes")
            with mock.patch.object(client, "_send", side_effect=fake_send):
                await client.send_image(str(media), "caption", ["group-a"])

        self.assertIs(payloads[0]["force_multipart"], False)


if __name__ == "__main__":
    unittest.main()
