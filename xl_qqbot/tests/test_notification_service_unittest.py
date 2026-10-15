import asyncio
import tempfile
import unittest
from pathlib import Path

from aiohttp.test_utils import TestClient, TestServer

from bot_app.config import load_config
from bot_app.notification_service import (
    _DISPATCH_TASK_KEY,
    _STOP_KEY,
    NotificationClient,
    NotificationService,
    build_notification_app,
)
from bot_app.proactive_outbox import ProactiveOutbox


TOKEN = "t" * 40
DEBUG_GROUP = "debug-group"
TEST_GROUP = "test-group"
PRODUCTION_GROUP = "production-group"


class FakeSender:
    def __init__(self):
        self.started = False
        self.closed = False

    async def start(self):
        self.started = True

    async def close(self):
        self.closed = True

    async def send_text(self, group, text):
        return True

    async def send_image(self, path, content, groups):
        return {group: True for group in groups}


class NotificationServiceTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.data_dir = Path(self.temp_dir.name) / "notification-data"
        self.outbox = ProactiveOutbox(self.data_dir)
        self.service = NotificationService(
            tier="test",
            allowed_groups={DEBUG_GROUP, TEST_GROUP},
            outbox=self.outbox,
        )
        self.sender = FakeSender()

    async def asyncSetUp(self):
        self.app = build_notification_app(
            self.service, bearer_token=TOKEN, sender=self.sender
        )
        self.client = TestClient(TestServer(self.app))
        await self.client.start_server()

    async def asyncTearDown(self):
        await self.client.close()
        self.temp_dir.cleanup()

    def request_payload(self, *, recipient=TEST_GROUP, tier="test", messages=None):
        return {
            "event_key": "event-1",
            "tier": tier,
            "messages": messages
            or [
                {
                    "recipient": recipient,
                    "ordinal": 0,
                    "kind": "text",
                    "text": "公告内容",
                }
            ],
        }

    async def post(self, payload, token=TOKEN):
        return await self.client.post(
            "/notifications",
            json=payload,
            headers={"Authorization": f"Bearer {token}"},
        )

    async def test_authentication_is_required(self):
        response = await self.post(self.request_payload(), token="wrong-token")

        self.assertEqual(response.status, 401)
        self.assertEqual(self.outbox.count(), 0)

    async def test_test_notifier_rejects_production_tier_and_group(self):
        wrong_tier = await self.post(
            self.request_payload(recipient=TEST_GROUP, tier="production")
        )
        wrong_group = await self.post(
            self.request_payload(recipient=PRODUCTION_GROUP, tier="test")
        )

        self.assertEqual(wrong_tier.status, 403)
        self.assertEqual(wrong_group.status, 403)
        self.assertEqual(self.outbox.count(), 0)

    async def test_duplicate_request_is_idempotently_enqueued_once(self):
        payload = self.request_payload()

        first = await self.post(payload)
        second = await self.post(payload)
        first_body = await first.json()
        second_body = await second.json()

        self.assertEqual(first.status, 200)
        self.assertEqual(second.status, 200)
        self.assertEqual(first_body["queued"], second_body["queued"])
        self.assertEqual(self.outbox.count(), 1)

    async def test_messages_for_one_group_are_queued_in_ordinal_fifo_order(self):
        response = await self.post(
            self.request_payload(
                messages=[
                    {
                        "recipient": TEST_GROUP,
                        "ordinal": 1,
                        "kind": "text",
                        "text": "第二条",
                    },
                    {
                        "recipient": TEST_GROUP,
                        "ordinal": 0,
                        "kind": "text",
                        "text": "第一条",
                    },
                ]
            )
        )

        self.assertEqual(response.status, 200)
        heads = self.outbox.next_due_heads(now=0)
        self.assertEqual([item.text for item in heads], ["第一条"])
        self.outbox.mark_sent(heads[0].sequence)
        next_head = self.outbox.next_due_heads(now=0)
        self.assertEqual([item.text for item in next_head], ["第二条"])

    async def test_image_request_copies_media_to_durable_spool(self):
        source = Path(self.temp_dir.name) / "schedule.png"
        source.write_bytes(b"schedule-image")

        response = await self.post(
            self.request_payload(
                messages=[
                    {
                        "recipient": DEBUG_GROUP,
                        "ordinal": 0,
                        "kind": "image",
                        "media_path": str(source),
                        "text": "图片说明",
                    }
                ]
            )
        )
        body = await response.json()
        message = self.outbox.get(body["queued"][0]["sequence"])

        self.assertEqual(response.status, 200)
        self.assertEqual(message.kind, "image")
        self.assertEqual((self.data_dir / message.media_path).read_bytes(), b"schedule-image")

    async def test_invalid_json_contract_is_rejected_without_queue_writes(self):
        response = await self.post({"event_key": "event-1", "tier": "test"})

        self.assertEqual(response.status, 400)
        self.assertEqual(self.outbox.count(), 0)

    async def test_notification_client_reports_durable_acceptance(self):
        client = NotificationClient(
            str(self.client.make_url("/")), bearer_token=TOKEN
        )
        await client.start()
        try:
            queued = await client.enqueue(
                event_key="client-event-1",
                tier="test",
                messages=[
                    {
                        "recipient": TEST_GROUP,
                        "ordinal": 0,
                        "kind": "text",
                        "text": "来自 producer",
                    }
                ],
            )
        finally:
            await client.close()

        self.assertEqual(len(queued), 1)
        self.assertEqual(self.outbox.count(), 1)

    async def test_notification_client_reports_service_readiness(self):
        client = NotificationClient(
            str(self.client.make_url("/")), bearer_token=TOKEN
        )
        await client.start()
        try:
            ready = await client.health()
        finally:
            await client.close()

        self.assertTrue(ready)

    async def test_health_fails_when_notification_dispatcher_stops(self):
        self.app[_STOP_KEY].set()
        await asyncio.wait_for(self.app[_DISPATCH_TASK_KEY], timeout=1)

        response = await self.client.get("/health")

        self.assertEqual(response.status, 503)
        self.assertFalse((await response.json())["ready"])


class NotificationConfigTests(unittest.TestCase):
    def make_config(self, notification_settings):
        contents = f'''
[bot]
appid = "1"
secret = "secret"

[watch]
outbox_dir = "/tmp/xl/outbox"
data_dir = "/tmp/xl/data"

[notification]
{notification_settings}
'''
        config_path = Path(self.temp_dir.name) / "config.toml"
        config_path.write_text(contents, encoding="utf-8")
        return load_config(str(config_path))

    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()

    def tearDown(self):
        self.temp_dir.cleanup()

    def test_notification_token_and_ports_are_loaded(self):
        config = self.make_config(
            f'api_token = "{TOKEN}"\ntest_port = 8786\nproduction_port = 8787'
        )

        self.assertEqual(config.notification.api_token, TOKEN)
        self.assertEqual(config.notification.test_port, 8786)
        self.assertEqual(config.notification.production_port, 8787)

    def test_notification_port_cannot_collide_with_worker(self):
        with self.assertRaisesRegex(ValueError, "notification.*服务端口"):
            self.make_config(f'api_token = "{TOKEN}"\ntest_port = 8782')

    def test_gateway_control_port_and_token_are_loaded(self):
        config_path = Path(self.temp_dir.name) / "gateway-config.toml"
        config_path.write_text(
            '[bot]\nappid="1"\nsecret="secret"\n'
            '[watch]\noutbox_dir="/tmp/xl/outbox"\n'
            '[gateway]\ncontrol_port=8785\ncontrol_token="' + TOKEN + '"\n',
            encoding="utf-8",
        )

        config = load_config(str(config_path))

        self.assertEqual(config.gateway.control_port, 8785)
        self.assertEqual(config.gateway.control_token, TOKEN)

    def test_gateway_control_port_cannot_collide_with_worker(self):
        config_path = Path(self.temp_dir.name) / "gateway-config.toml"
        config_path.write_text(
            '[bot]\nappid="1"\nsecret="secret"\n'
            '[watch]\noutbox_dir="/tmp/xl/outbox"\n'
            '[gateway]\ncontrol_port=8782\ncontrol_token="' + TOKEN + '"\n',
            encoding="utf-8",
        )

        with self.assertRaisesRegex(ValueError, "gateway.*服务端口"):
            load_config(str(config_path))

    def test_internal_api_bearer_tokens_must_match_when_configured(self):
        config_path = Path(self.temp_dir.name) / "token-config.toml"
        config_path.write_text(
            '[bot]\nappid="1"\nsecret="secret"\n'
            '[watch]\noutbox_dir="/tmp/xl/outbox"\n'
            '[router]\ndeployment_token="' + TOKEN + '"\n'
            '[gateway]\ncontrol_token="' + ("g" * 40) + '"\n'
            '[notification]\napi_token="' + TOKEN + '"\n',
            encoding="utf-8",
        )

        with self.assertRaisesRegex(ValueError, "internal API bearer tokens must match"):
            load_config(str(config_path))


if __name__ == "__main__":
    unittest.main()
