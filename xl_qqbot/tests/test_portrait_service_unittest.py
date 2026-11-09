"""级别服务装配立绘查询：按本实例 updater 数据根装配，各实例路径独立。"""

import hashlib
import json

import pytest
from aiohttp.test_utils import TestClient, TestServer

from bot_app.config import (
    BotConfig,
    Config,
    MessageConfig,
    PortraitConfig,
    TargetConfig,
    UploadConfig,
    WatchConfig,
)
from bot_app.service import build_app

PNG_2X3 = (
    b"\x89PNG\r\n\x1a\n"
    + b"\x00\x00\x00\x0dIHDR"
    + b"\x00\x00\x00\x02\x00\x00\x00\x03\x08\x06\x00\x00\x00"
    + b"\x00\x00\x00\x00IEND\xaeB`\x82"
)
PNG_SHA = hashlib.sha256(PNG_2X3).hexdigest()


class FakeSender:
    def __init__(self):
        self.calls = []

    async def start(self):
        pass

    async def close(self):
        pass

    async def send_text(self, group, text, reply_to=""):
        self.calls.append(("text", group, text))
        return True

    async def send_image(self, file_path, content, groups, reply_to=""):
        self.calls.append(("image", list(groups), file_path))
        return {g: True for g in groups}

    async def send_schedule_image(self, file_path, content, groups, reply_to=""):
        self.calls.append(("schedule_image", list(groups), file_path, content))
        return {g: True for g in groups}


def make_config(root, enabled=True):
    """一份完整实例数据：角色数据 + 图鉴卡片 + 立绘 manifest/PNG。"""
    char_file = root / "character_data" / "current.json"
    char_file.parent.mkdir(parents=True, exist_ok=True)
    char_file.write_text(
        json.dumps(
            {"characters": {"10000101": {"name": "菲尼斯/Finis", "star": 5}}},
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    versions_dir = root / "versions"
    cards = versions_dir / "123" / "character_cards"
    cards.mkdir(parents=True)
    (cards / "10000101_角色档案_长图.png").write_bytes(b"png")
    (root / "current_version.json").write_text('{"version": 123}', encoding="utf-8")
    portraits = versions_dir / "123" / "portraits"
    sub = portraits / "10000101"
    sub.mkdir(parents=True, exist_ok=True)
    (sub / "1000010101.png").write_bytes(PNG_2X3)
    (portraits / "manifest.json").write_text(
        json.dumps({
            "schema_version": 1,
            "version": "123",
            "skins": [{
                "character_id": "10000101",
                "skin_id": "1000010101",
                "ordinal": 1,
                "name": "菲尼斯",
                "input_fingerprint": "fp-1",
                "state": "published",
                "artifact_path": "10000101/1000010101.png",
                "artifact_sha256": PNG_SHA,
                "source_version": 123,
                "width": 2,
                "height": 3,
                "error": None,
            }],
        }),
        encoding="utf-8",
    )

    sender = FakeSender()
    config = Config(
        bot=BotConfig(appid="1", secret="2"),
        watch=WatchConfig(
            outbox_dir="/tmp/outbox", interval_seconds=30, data_dir=str(root / "bot_data"),
            character_data=str(char_file), versions_dir=str(versions_dir),
        ),
        target=TargetConfig(group_openids=[], auto_learn_from_events=False),
        upload=UploadConfig(file_base_url=""),
        message=MessageConfig(template=""),
        portraits=PortraitConfig(enabled=enabled),
    )
    return config, sender


def patch_sender_factory(monkeypatch):
    """QQSender 工厂按 config 身份返回对应 fake，支持同进程多实例。"""
    senders: dict[int, FakeSender] = {}

    def factory(config, instance_id=None):
        return senders[id(config)]

    monkeypatch.setattr("bot_app.service.QQSender", factory)
    return senders


AT_PAYLOAD = {
    "type": "group_at",
    "muted": False,
    "data": {"id": "m1", "group_openid": "g1", "content": "<@BOT> 菲尼斯1",
             "mentions": [], "author": {"member_openid": "u1"}},
}


async def post(client, payload):
    resp = await client.post("/event", json=payload)
    assert resp.status == 200


@pytest.mark.asyncio
async def test_instance_worker_serves_portrait_query(tmp_path, monkeypatch):
    senders = patch_sender_factory(monkeypatch)
    config, sender = make_config(tmp_path / "server_data", enabled=True)
    senders[id(config)] = sender
    app = build_app(config)
    client = TestClient(TestServer(app))
    await client.start_server()
    try:
        await post(client, AT_PAYLOAD)
        assert [c[0] for c in sender.calls] == ["schedule_image"]
        assert sender.calls[0][2].endswith("1000010101.png")
    finally:
        await client.close()


@pytest.mark.asyncio
async def test_portrait_feature_disabled_by_config(tmp_path, monkeypatch):
    senders = patch_sender_factory(monkeypatch)
    config, sender = make_config(tmp_path / "server_data", enabled=False)
    senders[id(config)] = sender
    app = build_app(config)
    client = TestClient(TestServer(app))
    await client.start_server()
    try:
        await post(client, AT_PAYLOAD)
        kinds = [c[0] for c in sender.calls]
        assert "schedule_image" not in kinds
        # 降级为现有匹配器行为：模糊命中图鉴（"菲尼斯1" → guess 菲尼斯）
        assert kinds == ["text", "image"]
        assert sender.calls[0][2] == "已为你匹配到：菲尼斯"
    finally:
        await client.close()


@pytest.mark.asyncio
async def test_instances_use_their_own_updater_roots(tmp_path, monkeypatch):
    """两个实例各自读自己的 versions_dir/character_data，互不串数据。"""
    senders = patch_sender_factory(monkeypatch)
    config_a, sender_a = make_config(tmp_path / "a" / "server_data", enabled=True)
    config_b, sender_b = make_config(tmp_path / "b" / "server_data", enabled=True)
    senders[id(config_a)] = sender_a
    senders[id(config_b)] = sender_b
    # B 根的立绘 PNG 内容与 A 的 manifest 哈希不同（B 删掉 PNG 后 A 仍可用即证明未串读）
    (tmp_path / "b" / "server_data" / "versions" / "123" / "portraits"
     / "10000101" / "1000010101.png").unlink()

    client_a = TestClient(TestServer(build_app(config_a)))
    client_b = TestClient(TestServer(build_app(config_b)))
    await client_a.start_server()
    await client_b.start_server()
    try:
        await post(client_a, AT_PAYLOAD)
        await post(client_b, AT_PAYLOAD)
        assert [c[0] for c in sender_a.calls] == ["schedule_image"]  # A 正常
        kinds_b = [c[0] for c in sender_b.calls]
        assert "schedule_image" not in kinds_b  # B 缺图只提示
        assert sender_b.calls[-1][0] == "text"
    finally:
        await client_a.close()
        await client_b.close()
