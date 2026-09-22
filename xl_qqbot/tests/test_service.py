"""级别服务 HTTP 契约：/health 与 /event（含 muted 语义）。"""

import json

import pytest
from aiohttp.test_utils import TestClient, TestServer

from bot_app.config import (
    BotConfig,
    Config,
    MessageConfig,
    TargetConfig,
    UploadConfig,
    WatchConfig,
)
from bot_app.service import QUERY_HANDLER_KEY, build_app, parse_args

CHARACTERS = {
    "10000101": {"name": "菲尼斯/Finis", "star": 5},
}


class FakeSender:
    def __init__(self):
        self.calls = []
        self.started = False
        self.closed = False

    async def start(self):
        self.started = True

    async def close(self):
        self.closed = True

    async def send_text(self, group, text, reply_to=""):
        self.calls.append(("text", group, text))
        return True

    async def send_image(self, file_path, content, groups, reply_to=""):
        self.calls.append(("image", list(groups), file_path))
        return {g: True for g in groups}


def make_config(tmp_path, monkeypatch):
    data_dir = tmp_path / "data"
    char_file = data_dir / "character_data" / "current.json"
    char_file.parent.mkdir(parents=True, exist_ok=True)
    char_file.write_text(
        json.dumps({"characters": CHARACTERS}, ensure_ascii=False), encoding="utf-8"
    )
    versions_dir = data_dir / "versions"
    cards = versions_dir / "123" / "character_cards"
    cards.mkdir(parents=True)
    (cards / "10000101_角色档案_长图.png").write_bytes(b"png")
    (data_dir / "current_version.json").write_text('{"version": 123}', encoding="utf-8")

    sender = FakeSender()
    # 替换 QQSender 为 fake（build_app 内部会 import 级使用）
    monkeypatch.setattr("bot_app.service.QQSender", lambda config: sender)
    return Config(
        bot=BotConfig(appid="1", secret="2"),
        watch=WatchConfig(
            outbox_dir="/tmp/outbox", interval_seconds=30, data_dir=str(data_dir),
            character_data=str(char_file), versions_dir=str(versions_dir),
        ),
        target=TargetConfig(group_openids=[], auto_learn_from_events=False),
        upload=UploadConfig(file_base_url=""),
        message=MessageConfig(template=""),
    ), sender


async def start_client(app):
    client = TestClient(TestServer(app))
    await client.start_server()
    return client


@pytest.mark.asyncio
async def test_health_endpoint(tmp_path, monkeypatch):
    config, sender = make_config(tmp_path, monkeypatch)
    client = await start_client(build_app(config))
    try:
        resp = await client.get("/health")
        assert resp.status == 200
        assert await resp.text() == "ok"
    finally:
        await client.close()


@pytest.mark.asyncio
async def test_event_at_message_full_chain(tmp_path, monkeypatch):
    config, sender = make_config(tmp_path, monkeypatch)
    client = await start_client(build_app(config))
    try:
        payload = {
            "type": "group_at",
            "muted": False,
            "data": {"id": "m1", "group_openid": "g1", "content": "<@BOT> 菲尼斯",
                     "mentions": [], "author": {"member_openid": "u1"}},
        }
        resp = await client.post("/event", json=payload)
        assert resp.status == 200
        assert (await resp.json())["ok"] is True
        assert [c[0] for c in sender.calls] == ["image"]  # 查询全链路已处理
    finally:
        await client.close()


@pytest.mark.asyncio
async def test_event_muted_ignores_query(tmp_path, monkeypatch):
    config, sender = make_config(tmp_path, monkeypatch)
    client = await start_client(build_app(config))
    try:
        payload = {"type": "group_at", "muted": True,
                   "data": {"group_openid": "g1", "content": "<@BOT> 菲尼斯",
                            "author": {"member_openid": "u1"}}}
        resp = await client.post("/event", json=payload)
        assert resp.status == 200
        assert sender.calls == []
    finally:
        await client.close()


@pytest.mark.asyncio
async def test_event_digit_selection_roundtrip(tmp_path, monkeypatch):
    config, sender = make_config(tmp_path, monkeypatch)
    client = await start_client(build_app(config))
    try:
        at = {"id": "m1", "group_openid": "g1", "content": "<@BOT> 菲尼斯",
              "mentions": [], "author": {"member_openid": "u1"}}
        resp = await client.post("/event", json={"type": "group_at", "muted": False, "data": at})
        assert resp.status == 200
        # 候选选择不存在于这张角色表，直接命中发图；用不存在的角色制造候选路径太重，
        # 这里验证数字消息被正常路由消费即可
        resp = await client.post("/event", json={
            "type": "group_message", "muted": False,
            "data": {"id": "m2", "group_openid": "g1", "content": "1",
                     "author": {"member_openid": "u1"}},
        })
        assert resp.status == 200
    finally:
        await client.close()


@pytest.mark.asyncio
async def test_event_invalid_json_400(tmp_path, monkeypatch):
    config, sender = make_config(tmp_path, monkeypatch)
    client = await start_client(build_app(config))
    try:
        resp = await client.post("/event", data=b"not-json")
        assert resp.status == 400
    finally:
        await client.close()


@pytest.mark.asyncio
async def test_event_handler_error_500(tmp_path, monkeypatch):
    config, sender = make_config(tmp_path, monkeypatch)

    async def boom(payload):
        raise RuntimeError("炸了")

    app = build_app(config)
    monkeypatch.setattr(app[QUERY_HANDLER_KEY], "handle", boom)
    client = await start_client(app)
    try:
        resp = await client.post("/event", json={"type": "group_at", "data": {}})
        assert resp.status == 500
        assert (await resp.json())["ok"] is False
    finally:
        await client.close()


@pytest.mark.asyncio
async def test_startup_logs_in_sender_and_cleanup_closes(tmp_path, monkeypatch):
    config, sender = make_config(tmp_path, monkeypatch)
    client = await start_client(build_app(config))
    try:
        assert sender.started  # on_startup 已登录
    finally:
        await client.close()
    assert sender.closed  # on_cleanup 已关闭


# ---------- 启动参数 ----------

def test_parse_args_requires_tier_and_port():
    args = parse_args(["--tier", "debug", "--port", "8781"])
    assert args.tier == "debug" and args.port == 8781

    with pytest.raises(SystemExit):
        parse_args(["--port", "8781"])  # 缺 --tier
    with pytest.raises(SystemExit):
        parse_args(["--tier", "verbose", "--port", "1"])  # 非法级别
