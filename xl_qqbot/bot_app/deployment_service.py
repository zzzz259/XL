"""Deployment API that coordinates tier notification services and the Gateway."""

from __future__ import annotations

import argparse
import asyncio
import hmac
import json
import logging
import re
from collections.abc import Callable, Iterable

from aiohttp import web

from .config import Config, load_config
from .gateway import GatewayControlClient
from .groups import GroupStore
from .notification_service import NotificationClient
from .tiers import GroupTier

_logger = logging.getLogger(__name__)
_TIERS = ("debug", "test", "production")
_ANNOUNCE_TIERS = ("debug", "test", "main")
_ANNOUNCEMENTS = {
    "starting": "检测到更新，正在更新bot，期间将暂停服务",
    "complete": "更新完毕",
}
_START_NOTICE_DELIVERY_TIMEOUT_SECONDS = 30


def build_deployment_service_app(
    *,
    bearer_token: str,
    tiers,
    target_groups: Iterable[str] | Callable[[], Iterable[str]],
    gateway_client,
    test_notification_client,
    main_notification_client,
) -> web.Application:
    """Build the 8784 control API while keeping Gateway and queue state remote."""
    if not isinstance(bearer_token, str) or len(bearer_token) < 32:
        raise ValueError("deployment bearer token must be at least 32 characters")

    @web.middleware
    async def authenticate(request: web.Request, handler):
        expected = bearer_token.encode("utf-8")
        scheme, separator, supplied_value = request.headers.get(
            "Authorization", ""
        ).partition(" ")
        supplied = (
            supplied_value.encode("utf-8")
            if separator and scheme.lower() == "bearer"
            else b""
        )
        if not hmac.compare_digest(supplied, expected):
            return web.json_response({"ok": False, "error": "unauthorized"}, status=401)
        return await handler(request)

    async def health(_request: web.Request) -> web.Response:
        dependencies = await asyncio.gather(
            gateway_client.health(),
            test_notification_client.health(),
            main_notification_client.health(),
            return_exceptions=True,
        )
        ready = all(result is True for result in dependencies)
        return web.json_response(
            {"ok": ready, "ready": ready}, status=200 if ready else 503
        )

    async def announce(request: web.Request) -> web.Response:
        try:
            payload = await request.json()
        except (ValueError, json.JSONDecodeError, web.HTTPException):
            return web.json_response({"ok": False, "error": "invalid json"}, status=400)
        if not isinstance(payload, dict):
            return web.json_response(
                {"ok": False, "error": "expected object"}, status=400
            )
        tier = payload.get("tier")
        phase = payload.get("phase")
        notification_id = payload.get("notification_id")
        if tier not in _ANNOUNCE_TIERS or phase not in (*_ANNOUNCEMENTS, "release_note"):
            return web.json_response(
                {"ok": False, "error": "invalid tier or phase"}, status=400
            )
        if (
            not isinstance(notification_id, str)
            or re.fullmatch(r"[A-Za-z0-9._:-]{1,200}", notification_id) is None
        ):
            return web.json_response(
                {"ok": False, "error": "invalid notification_id"}, status=400
            )
        if phase == "release_note":
            text = payload.get("text")
            if not isinstance(text, str) or not text.strip() or len(text) > 4000:
                return web.json_response(
                    {"ok": False, "error": "invalid release note"}, status=400
                )
            text = text.strip()
        else:
            text = _ANNOUNCEMENTS[phase]

        try:
            configured_groups = (
                target_groups() if callable(target_groups) else target_groups
            )
            notice_groups = sorted(
                {
                    group
                    for group in configured_groups
                    if tiers.tier_of(group)
                    == ("production" if tier == "main" else tier)
                }
            )
        except Exception:
            _logger.exception("部署公告目标群解析失败 tier=%s", tier)
            return web.json_response(
                {"ok": False, "error": "target resolution failed", "results": {}},
                status=503,
            )
        if not notice_groups:
            return web.json_response({"ok": False, "results": {}})

        notification_client = (
            main_notification_client if tier == "main" else test_notification_client
        )
        messages = [
            {
                "recipient": group,
                "ordinal": 0,
                "kind": "text",
                "text": text,
            }
            for group in notice_groups
        ]
        try:
            event_key = f"deployment:{notification_id}"
            queued = await notification_client.enqueue(
                event_key=event_key,
                tier=tier,
                messages=messages,
            )
            if len(queued) != len(messages):
                raise RuntimeError("notification API returned an incomplete queue receipt")
            if phase == "starting" and not await notification_client.wait_for_delivery(
                event_key,
                expected_total=len(messages),
                timeout_seconds=_START_NOTICE_DELIVERY_TIMEOUT_SECONDS,
            ):
                raise RuntimeError("start notice was not delivered before timeout")
        except Exception:
            _logger.exception(
                "部署公告入队或发送确认失败 tier=%s id=%s", tier, notification_id
            )
            results = {group: False for group in notice_groups}
            return web.json_response(
                {"ok": False, "results": results}, status=503
            )
        return web.json_response(
            {"ok": True, "results": {group: True for group in notice_groups}}
        )

    async def maintenance(request: web.Request) -> web.Response:
        try:
            payload = await request.json()
        except (ValueError, json.JSONDecodeError, web.HTTPException):
            return web.json_response({"ok": False, "error": "invalid json"}, status=400)
        if not isinstance(payload, dict):
            return web.json_response(
                {"ok": False, "error": "expected object"}, status=400
            )
        tier = payload.get("tier")
        enabled = payload.get("enabled")
        if tier not in (*_TIERS, "main") or not isinstance(enabled, bool):
            return web.json_response(
                {"ok": False, "error": "invalid tier or enabled"}, status=400
            )
        try:
            result = await gateway_client.set_maintenance(tier, enabled)
        except Exception:
            _logger.exception("Gateway 维护状态更新失败 tier=%s", tier)
            return web.json_response(
                {"ok": False, "error": "gateway unavailable"}, status=503
            )
        affected_tiers = _TIERS if tier == "main" else (tier,)
        states = result.get("tiers", result) if isinstance(result, dict) else {}
        return web.json_response(
            {
                "ok": True,
                "tier": tier,
                "enabled": enabled,
                "tiers": {
                    item: states.get(item, enabled) for item in affected_tiers
                },
            }
        )

    async def drained(request: web.Request) -> web.Response:
        tier = request.query.get("tier", "")
        if tier not in (*_TIERS, "main"):
            return web.json_response({"ok": False, "error": "invalid tier"}, status=400)
        try:
            result = await gateway_client.drained(tier)
        except Exception:
            _logger.exception("Gateway drain 状态查询失败 tier=%s", tier)
            return web.json_response(
                {"ok": False, "error": "gateway unavailable"}, status=503
            )
        return web.json_response(
            {
                "ok": True,
                "tier": tier,
                "drained": result.get("drained") is True,
                "active": int(result.get("active", 0)),
            }
        )

    app = web.Application(middlewares=[authenticate])
    app.router.add_get("/health", health)
    app.router.add_post("/deployment/announce", announce)
    app.router.add_post("/deployment/maintenance", maintenance)
    app.router.add_get("/deployment/drained", drained)
    return app


def parse_args(argv=None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="xl_qqbot deployment control service")
    return parser.parse_args(argv)


def _configured_target_groups(config: Config) -> tuple[str, ...]:
    if config.target.group_openids:
        return tuple(sorted(set(config.target.group_openids)))
    return tuple(sorted(GroupStore(config.watch.data_dir).openids()))


def main(argv=None) -> None:
    parse_args(argv)
    config = load_config("config.toml")
    if len(config.router.deployment_token) < 32:
        raise ValueError("[router] deployment_token must be configured with 32+ characters")
    if len(config.notification.api_token) < 32:
        raise ValueError("[notification] api_token must be configured with 32+ characters")
    if len(config.gateway.control_token) < 32:
        raise ValueError("[gateway] control_token must be configured with 32+ characters")

    gateway_client = GatewayControlClient(
        f"http://127.0.0.1:{config.gateway.control_port}",
        bearer_token=config.gateway.control_token,
    )
    test_notification_client = NotificationClient(
        f"http://127.0.0.1:{config.notification.test_port}",
        bearer_token=config.notification.api_token,
    )
    main_notification_client = NotificationClient(
        f"http://127.0.0.1:{config.notification.production_port}",
        bearer_token=config.notification.api_token,
    )
    app = build_deployment_service_app(
        bearer_token=config.router.deployment_token,
        tiers=GroupTier(config.groups),
        target_groups=lambda: _configured_target_groups(config),
        gateway_client=gateway_client,
        test_notification_client=test_notification_client,
        main_notification_client=main_notification_client,
    )

    async def start_clients(_app: web.Application) -> None:
        await asyncio.gather(
            gateway_client.start(),
            test_notification_client.start(),
            main_notification_client.start(),
        )

    async def close_clients(_app: web.Application) -> None:
        await asyncio.gather(
            gateway_client.close(),
            test_notification_client.close(),
            main_notification_client.close(),
        )

    app.on_startup.append(start_clients)
    app.on_cleanup.append(close_clients)
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    )
    web.run_app(
        app,
        host="127.0.0.1",
        port=config.router.deployment_control_port,
    )


if __name__ == "__main__":
    main()
