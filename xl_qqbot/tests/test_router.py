"""路由器：分级转发目标选择、muted 注入、服务挂掉不崩、群学习。"""

import asyncio
import json
import os
import tempfile

import aiohttp
import pytest

from bot_app.config import (
    BotConfig,
    Config,
    GroupsConfig,
    MessageConfig,
    RouterConfig,
    TargetConfig,
    UploadConfig,
    WatchConfig,
    load_config,
)
from bot_app.groups import GroupStore
from bot_app.router import TierForwarder, _RouterClient
from bot_app.tiers import GroupTier

DEBUG_G = "7D8F62B840FF97A1E3EF0E219575EFA8"
TEST_G = "C856F085818C52C0E850753B855B3452"
PROD_G = "AAAAF085818C52C0E850753B855B1111"


def write_toml(content):
    f = tempfile.NamedTemporaryFile(mode="w", suffix=".toml", delete=False, encoding="utf-8")
    f.write(content)
    f.close()
    return f.name


BASE_TOML = """
[bot]
appid = "123"
secret = "secret"

[watch]
outbox_dir = "/tmp/outbox"
"""


def test_config_router_defaults_and_parse():
    path = write_toml(BASE_TOML)
    try:
        cfg = load_config(path)
        assert cfg.router.debug_port == 8781
        assert cfg.router.test_port == 8782
        assert cfg.router.production_port == 8783
        assert cfg.router.forward_timeout == 8.0
    finally:
        os.unlink(path)

    path = write_toml(BASE_TOML + """
[router]
debug_port = 9001
test_port = 9002
production_port = 9003
forward_timeout = 3
""")
    try:
        cfg = load_config(path)
        assert (cfg.router.debug_port, cfg.router.test_port, cfg.router.production_port) == (9001, 9002, 9003)
        assert cfg.router.forward_timeout == 3.0
    finally:
        os.unlink(path)


def test_config_router_bad_timeout():
    path = write_toml(BASE_TOML + """
[router]
forward_timeout = 0
""")
    try:
        with pytest.raises(ValueError, match="forward_timeout"):
            load_config(path)
    finally:
        os.unlink(path)


# ---------- TierForwarder ----------

class FakeSession:
    def __init__(self, fail=None, status=200):
        self.fail = fail
        self.status = status
        self.posts = []
        self.closed = False

    def post(self, url, json=None):
        if self.fail:
            raise self.fail
        self.posts.append((url, json))
        return FakeResponse(self.status)

    async def close(self):
        self.closed = True


class FakeResponse:
    def __init__(self, status):
        self.status = status

    async def __aenter__(self):
        return self

    async def __aexit__(self, *args):
        return False


@pytest.mark.asyncio
async def test_forward_picks_port_by_tier():
    forwarder = TierForwarder({"debug": 8781, "test": 8782, "production": 8783}, timeout=8)
    forwarder._session = FakeSession()
    payload = {"type": "group_at", "muted": False, "data": {"group_openid": "g"}}

    for tier, port in (("debug", 8781), ("test", 8782), ("production", 8783)):
        assert await forwarder.forward(tier, payload)
    assert [p[0] for p in forwarder._session.posts] == [
        "http://127.0.0.1:8781/event",
        "http://127.0.0.1:8782/event",
        "http://127.0.0.1:8783/event",
    ]


@pytest.mark.asyncio
async def test_forward_service_down_only_logs_no_crash():
    forwarder = TierForwarder({"debug": 8781}, timeout=8)
    forwarder._session = FakeSession(fail=aiohttp.ClientConnectionError("连接拒绝"))
    ok = await forwarder.forward("debug", {"type": "group_at"})
    assert not ok  # 服务挂了：返回 False，不抛异常


@pytest.mark.asyncio
async def test_forward_timeout_error_swallowed():
    forwarder = TierForwarder({"debug": 8781}, timeout=0.001)
    forwarder._session = FakeSession(fail=asyncio.TimeoutError())
    assert not await forwarder.forward("debug", {"type": "group_at"})


@pytest.mark.asyncio
async def test_forward_non_200_logged_as_failure():
    forwarder = TierForwarder({"debug": 8781})
    forwarder._session = FakeSession(status=500)
    assert not await forwarder.forward("debug", {"type": "group_at"})


@pytest.mark.asyncio
async def test_forward_unknown_tier_drops():
    forwarder = TierForwarder({"debug": 8781})
    forwarder._session = FakeSession()
    assert not await forwarder.forward("verbose", {"type": "group_at"})
    assert forwarder._session.posts == []


# ---------- _RouterClient._route ----------

def make_router(tmp_path, features=None, events_parent=None):
    data_dir = tmp_path / "data"
    group_store = GroupStore(str(data_dir))
    tiers = GroupTier(GroupsConfig(
        debug=[DEBUG_G], test=[TEST_G], features=features or {},
    ))
    forwarder = TierForwarder({"debug": 8781, "test": 8782, "production": 8783})
    forwarder._session = FakeSession()
    # update_status.json 与 watcher 同一目录约定：outbox_dir 的父目录
    status_dir = tmp_path / "server_data"
    status_dir.mkdir(exist_ok=True)
    router = object.__new__(_RouterClient)
    router._group_store = group_store
    router._tiers = tiers
    router._forwarder = forwarder
    router._update_status_path = status_dir / "update_status.json"
    return router, forwarder, group_store


def last_payload(forwarder):
    return forwarder._session.posts[-1][1]


@pytest.mark.asyncio
async def test_route_selects_tier_by_group(tmp_path):
    router, forwarder, _ = make_router(tmp_path)
    for group, expected_port in ((DEBUG_G, 8781), (TEST_G, 8782), (PROD_G, 8783)):
        await router._route("group_message", {"group_openid": group, "content": "hi"})
        assert forwarder._session.posts[-1][0] == f"http://127.0.0.1:{expected_port}/event"
        assert last_payload(forwarder)["type"] == "group_message"
        assert last_payload(forwarder)["data"]["group_openid"] == group
        assert last_payload(forwarder)["muted"] is False


@pytest.mark.asyncio
async def test_route_learns_group(tmp_path):
    router, _, group_store = make_router(tmp_path)
    await router._route("group_message", {"group_openid": PROD_G, "content": "x"})
    assert PROD_G in group_store.openids()


@pytest.mark.asyncio
async def test_route_injects_muted_from_update_status(tmp_path):
    router, forwarder, _ = make_router(tmp_path)
    router._update_status_path.write_text(
        json.dumps({"updating": True, "version": 5}), encoding="utf-8"
    )
    await router._route("group_at", {"group_openid": PROD_G, "content": "<@b> 菲尼斯"})
    assert last_payload(forwarder)["muted"] is True
    # 更新结束后（文件改写）muted 恢复 False
    router._update_status_path.write_text(
        json.dumps({"updating": False, "version": 5}), encoding="utf-8"
    )
    await router._route("group_at", {"group_openid": PROD_G, "content": "<@b> 菲尼斯"})
    assert last_payload(forwarder)["muted"] is False


@pytest.mark.asyncio
async def test_route_add_robot_forwarded(tmp_path):
    router, forwarder, group_store = make_router(tmp_path)
    await router.on_group_add_robot({"group_openid": TEST_G})
    assert TEST_G in group_store.openids()  # 路由器负责群学习
    assert last_payload(forwarder)["type"] == "group_add_robot"


@pytest.mark.asyncio
async def test_route_missing_group_ignored(tmp_path):
    router, forwarder, _ = make_router(tmp_path)
    await router._route("group_message", {"content": "no group"})
    assert forwarder._session.posts == []


@pytest.mark.asyncio
async def test_route_service_down_router_survives(tmp_path):
    """整链路：服务端口无人监听也能正常返回（记 error 日志）。"""
    router, forwarder, _ = make_router(tmp_path)
    forwarder._session = FakeSession(fail=aiohttp.ClientConnectionError("服务未启动"))
    await router._route("group_message", {"group_openid": DEBUG_G, "content": "hi"})
    # 不抛异常即通过；后续事件照常处理
    forwarder._session = FakeSession()
    await router._route("group_message", {"group_openid": DEBUG_G, "content": "hi"})
    assert len(forwarder._session.posts) == 1
