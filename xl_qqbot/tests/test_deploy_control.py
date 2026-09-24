"""Deployment maintenance control state and loopback API tests."""

import asyncio
import json

import pytest
from aiohttp.test_utils import TestClient, TestServer
from bot_app.config import GroupsConfig
from bot_app.deploy_control import DeploymentControl, build_deployment_app
from bot_app.tiers import GroupTier

DEBUG_GROUP = "debug-group"
TEST_GROUP = "test-group"
PROD_GROUP = "production-group"
TOKEN = "a-long-random-deployment-token"


class RecordingSender:
    def __init__(self, failures=()):
        self.messages = []
        self.failures = set(failures)

    async def send_text(self, group_openid, text):
        self.messages.append((group_openid, text))
        return group_openid not in self.failures


def test_missing_maintenance_file_defaults_all_tiers_to_unmaintained(tmp_path):
    control = DeploymentControl(tmp_path / "maintenance.json")

    assert all(
        not control.is_maintained(tier)
        for tier in ("debug", "test", "production")
    )


@pytest.mark.parametrize(
    "payload",
    [
        "{invalid json",
        "[]",
        "{}",
        '{"tiers": []}',
        '{"tiers": {"debug": true, "test": false}}',
        '{"tiers": {"debug": true, "test": false, "production": false, "staging": false}}',
        '{"tiers": {"debug": true, "test": false, "production": 0}}',
    ],
)
def test_invalid_persisted_maintenance_state_fails_startup(tmp_path, payload):
    path = tmp_path / "maintenance.json"
    path.write_text(payload, encoding="utf-8")

    with pytest.raises(RuntimeError, match="maintenance state"):
        DeploymentControl(path)


def test_unreadable_persisted_maintenance_state_fails_startup(tmp_path, monkeypatch):
    path = tmp_path / "maintenance.json"
    path.write_text('{"tiers": {"debug": true, "test": false, "production": false}}', encoding="utf-8")
    original_read_text = type(path).read_text

    def deny_state_read(candidate, *args, **kwargs):
        if candidate == path:
            raise PermissionError("simulated unreadable maintenance file")
        return original_read_text(candidate, *args, **kwargs)

    monkeypatch.setattr(type(path), "read_text", deny_state_read)

    with pytest.raises(RuntimeError, match="maintenance state"):
        DeploymentControl(path)


def make_app(control, sender, groups=None, features=None):
    return build_deployment_app(
        control=control,
        bearer_token=TOKEN,
        sender=sender,
        tiers=GroupTier(GroupsConfig(
            debug=[DEBUG_GROUP], test=[TEST_GROUP], features=features or {},
        )),
        target_groups=groups or [DEBUG_GROUP, TEST_GROUP, PROD_GROUP],
    )


@pytest.mark.asyncio
async def test_maintenance_state_is_atomic_and_persists_per_tier(tmp_path):
    path = tmp_path / "maintenance.json"
    control = DeploymentControl(path)

    await control.set_maintenance("debug", True)
    await control.set_maintenance("test", False)

    assert json.loads(path.read_text(encoding="utf-8")) == {
        "tiers": {"debug": True, "test": False, "production": False}
    }
    assert not list(tmp_path.glob("*.tmp"))
    restored = DeploymentControl(path)
    assert restored.is_maintained("debug")
    assert not restored.is_maintained("test")
    assert not restored.is_maintained("production")


@pytest.mark.asyncio
async def test_active_forward_prevents_maintenance_and_drain_waits_for_completion(tmp_path):
    control = DeploymentControl(tmp_path / "maintenance.json")
    assert await control.begin_forward("test")
    assert not await control.wait_drained("test", timeout=0.01)

    drain = asyncio.create_task(control.wait_drained("test", timeout=1))
    await control.end_forward("test")

    assert await drain
    assert await control.wait_drained("test", timeout=0)
    assert control.active_forwards("test") == 0


@pytest.mark.asyncio
async def test_maintenance_blocks_new_forwards_without_blocking_other_tiers(tmp_path):
    control = DeploymentControl(tmp_path / "maintenance.json")
    await control.set_maintenance("debug", True)

    assert not await control.begin_forward("debug")
    assert await control.begin_forward("test")
    await control.end_forward("test")


@pytest.mark.asyncio
async def test_announcement_uses_exact_text_and_only_target_tier_groups(tmp_path):
    control = DeploymentControl(tmp_path / "maintenance.json")
    sender = RecordingSender()
    client = TestClient(TestServer(make_app(control, sender)))
    await client.start_server()
    try:
        response = await client.post(
            "/deployment/announce",
            json={"tier": "debug", "phase": "starting"},
            headers={"Authorization": f"Bearer {TOKEN}"},
        )

        assert response.status == 200
        assert await response.json() == {"ok": True, "results": {DEBUG_GROUP: True}}
        assert sender.messages == [(DEBUG_GROUP, "检测到更新，正在更新bot，期间将暂停服务")]

        response = await client.post(
            "/deployment/announce",
            json={"tier": "test", "phase": "complete"},
            headers={"Authorization": f"Bearer {TOKEN}"},
        )
        assert await response.json() == {"ok": True, "results": {TEST_GROUP: True}}
        assert sender.messages[-1] == (TEST_GROUP, "更新完毕")
    finally:
        await client.close()


@pytest.mark.asyncio
async def test_main_router_announcement_targets_every_notice_enabled_group(tmp_path):
    control = DeploymentControl(tmp_path / "maintenance.json")
    sender = RecordingSender()
    client = TestClient(TestServer(make_app(
        control,
        sender,
        features={"update_notice": "test"},
    )))
    await client.start_server()
    try:
        response = await client.post(
            "/deployment/announce",
            json={"tier": "main", "phase": "starting"},
            headers={"Authorization": f"Bearer {TOKEN}"},
        )
        assert await response.json() == {
            "ok": True,
            "results": {DEBUG_GROUP: True, TEST_GROUP: True},
        }
        assert {group for group, _ in sender.messages} == {DEBUG_GROUP, TEST_GROUP}
    finally:
        await client.close()


@pytest.mark.asyncio
async def test_announcement_with_no_notice_enabled_groups_is_not_success(tmp_path):
    control = DeploymentControl(tmp_path / "maintenance.json")
    sender = RecordingSender()
    client = TestClient(TestServer(make_app(
        control,
        sender,
        groups=[TEST_GROUP, PROD_GROUP],
        features={"update_notice": "debug"},
    )))
    await client.start_server()
    try:
        response = await client.post(
            "/deployment/announce",
            json={"tier": "main", "phase": "starting"},
            headers={"Authorization": f"Bearer {TOKEN}"},
        )

        assert await response.json() == {"ok": False, "results": {}}
        assert sender.messages == []
    finally:
        await client.close()


@pytest.mark.asyncio
async def test_announcement_reports_each_group_send_failure(tmp_path):
    control = DeploymentControl(tmp_path / "maintenance.json")
    sender = RecordingSender(failures={DEBUG_GROUP})
    client = TestClient(TestServer(make_app(control, sender)))
    await client.start_server()
    try:
        response = await client.post(
            "/deployment/announce",
            json={"tier": "debug", "phase": "starting"},
            headers={"Authorization": f"Bearer {TOKEN}"},
        )
        assert await response.json() == {"ok": False, "results": {DEBUG_GROUP: False}}
    finally:
        await client.close()


@pytest.mark.asyncio
async def test_authenticated_release_note_announcement_uses_supplied_content(tmp_path):
    control = DeploymentControl(tmp_path / "maintenance.json")
    sender = RecordingSender()
    client = TestClient(TestServer(make_app(control, sender)))
    await client.start_server()
    try:
        response = await client.post(
            "/deployment/announce",
            json={"tier": "main", "phase": "release_note", "text": "版本更新内容"},
            headers={"Authorization": f"Bearer {TOKEN}"},
        )

        assert await response.json() == {
            "ok": True,
            "results": {DEBUG_GROUP: True, TEST_GROUP: True, PROD_GROUP: True},
        }
        assert set(sender.messages) == {
            (DEBUG_GROUP, "版本更新内容"),
            (TEST_GROUP, "版本更新内容"),
            (PROD_GROUP, "版本更新内容"),
        }
    finally:
        await client.close()


@pytest.mark.asyncio
async def test_control_api_rejects_missing_or_invalid_bearer_token(tmp_path):
    client = TestClient(TestServer(make_app(
        DeploymentControl(tmp_path / "maintenance.json"), RecordingSender(),
    )))
    await client.start_server()
    try:
        for headers in ({}, {"Authorization": "Bearer wrong-token"}):
            response = await client.get("/health", headers=headers)
            assert response.status == 401
    finally:
        await client.close()


@pytest.mark.asyncio
async def test_maintenance_api_only_changes_forwarding_state_and_reports_drain(tmp_path):
    control = DeploymentControl(tmp_path / "maintenance.json")
    sender = RecordingSender()
    client = TestClient(TestServer(make_app(control, sender)))
    await client.start_server()
    try:
        response = await client.post(
            "/deployment/maintenance",
            json={"tier": "debug", "enabled": True},
            headers={"Authorization": f"Bearer {TOKEN}"},
        )
        assert (await response.json())["ok"] is True
        assert control.is_maintained("debug")
        assert control.ready is False  # maintenance control does not stop a service

        response = await client.get(
            "/deployment/drained?tier=main",
            headers={"Authorization": f"Bearer {TOKEN}"},
        )
        assert await response.json() == {
            "ok": True, "tier": "main", "drained": True, "active": 0,
        }
    finally:
        await client.close()


@pytest.mark.asyncio
async def test_main_maintenance_updates_all_tiers_as_one_persisted_change(tmp_path, monkeypatch):
    control = DeploymentControl(tmp_path / "maintenance.json")
    original_save = control._save
    save_calls = 0

    def fail_on_second_save(tiers):
        nonlocal save_calls
        save_calls += 1
        if save_calls == 2:
            raise OSError("simulated disk error")
        original_save(tiers)

    monkeypatch.setattr(control, "_save", fail_on_second_save)
    client = TestClient(TestServer(make_app(control, RecordingSender())))
    await client.start_server()
    try:
        response = await client.post(
            "/deployment/maintenance",
            json={"tier": "main", "enabled": True},
            headers={"Authorization": f"Bearer {TOKEN}"},
        )

        assert response.status == 200
        assert await response.json() == {
            "ok": True,
            "tier": "main",
            "enabled": True,
            "tiers": {"debug": True, "test": True, "production": True},
        }
        assert save_calls == 1
        assert all(control.is_maintained(tier) for tier in ("debug", "test", "production"))
    finally:
        await client.close()


@pytest.mark.asyncio
async def test_health_reflects_router_readiness(tmp_path):
    control = DeploymentControl(tmp_path / "maintenance.json")
    client = TestClient(TestServer(make_app(control, RecordingSender())))
    await client.start_server()
    try:
        response = await client.get(
            "/health", headers={"Authorization": f"Bearer {TOKEN}"},
        )
        assert response.status == 503
        control.ready = True
        response = await client.get(
            "/health", headers={"Authorization": f"Bearer {TOKEN}"},
        )
        assert response.status == 200
        assert await response.json() == {"ok": True, "ready": True}
    finally:
        await client.close()
