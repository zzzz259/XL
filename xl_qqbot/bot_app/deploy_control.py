"""Loopback-only deployment readiness, maintenance, drain, and notice API."""

from __future__ import annotations

import asyncio
import hmac
import json
import logging
import os
import re
import tempfile
from collections.abc import Callable, Iterable
from pathlib import Path

from aiohttp import web

from .proactive_outbox import ProactiveOutbox

_logger = logging.getLogger(__name__)
_TIERS = ("debug", "test", "production")
_ANNOUNCE_TIERS = ("debug", "test", "main")
_ANNOUNCEMENTS = {
    "starting": "检测到更新，正在更新bot，期间将暂停服务",
    "complete": "更新完毕",
}


class DeploymentControl:
    """Persistent per-tier maintenance state and in-flight forward accounting."""

    def __init__(self, maintenance_path: str | Path):
        self.path = Path(maintenance_path)
        self._condition = asyncio.Condition()
        self._maintenance = {tier: False for tier in _TIERS}
        self._active = {tier: 0 for tier in _TIERS}
        self.ready = False
        self._load()

    def _load(self) -> None:
        try:
            contents = self.path.read_text(encoding="utf-8")
        except FileNotFoundError:
            # A genuinely absent first-run file uses the safe all-disabled default.
            return
        except OSError as exc:
            raise RuntimeError(
                f"cannot read maintenance state at {self.path}: {exc}"
            ) from exc

        try:
            payload = json.loads(contents)
            if not isinstance(payload, dict):
                raise TypeError("top-level value must be an object")
            tiers = payload.get("tiers")
            if not isinstance(tiers, dict):
                raise TypeError("'tiers' must be an object")
            if set(tiers) != set(_TIERS):
                missing = sorted(set(_TIERS) - set(tiers))
                unknown = sorted(set(tiers) - set(_TIERS))
                raise ValueError(
                    f"tier keys must be exactly {_TIERS}; "
                    f"missing={missing}, unknown={unknown}"
                )
            if any(not isinstance(value, bool) for value in tiers.values()):
                raise ValueError("every tier state must be a boolean")
        except (json.JSONDecodeError, ValueError, TypeError) as exc:
            raise RuntimeError(
                f"invalid maintenance state at {self.path}: {exc}"
            ) from exc

        # Do not expose partially validated state if any persisted tier is invalid.
        self._maintenance = {tier: tiers[tier] for tier in _TIERS}

    def _save(self, tiers: dict[str, bool]) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        temporary_path = None
        try:
            with tempfile.NamedTemporaryFile(
                mode="w",
                encoding="utf-8",
                dir=self.path.parent,
                prefix=f"{self.path.name}.",
                suffix=".tmp",
                delete=False,
            ) as temporary:
                temporary_path = Path(temporary.name)
                json.dump({"tiers": tiers}, temporary, sort_keys=True)
                temporary.flush()
                os.fsync(temporary.fileno())
            os.replace(temporary_path, self.path)
        finally:
            if temporary_path is not None and temporary_path.exists():
                temporary_path.unlink()

    def is_maintained(self, tier: str) -> bool:
        self._validate_tier(tier)
        return self._maintenance[tier]

    async def set_maintenance(self, tier: str, enabled: bool) -> None:
        await self.set_maintenance_many((tier,), enabled)

    async def set_maintenance_many(self, tiers: Iterable[str], enabled: bool) -> None:
        tiers = tuple(dict.fromkeys(tiers))
        if not tiers:
            raise ValueError("at least one service tier is required")
        for tier in tiers:
            self._validate_tier(tier)
        if not isinstance(enabled, bool):
            raise TypeError("maintenance state must be a boolean")
        async with self._condition:
            updated = dict(self._maintenance)
            updated.update({tier: enabled for tier in tiers})
            self._save(updated)
            self._maintenance = updated
            self._condition.notify_all()

    async def begin_forward(self, tier: str) -> bool:
        self._validate_tier(tier)
        async with self._condition:
            if self._maintenance[tier]:
                return False
            self._active[tier] += 1
            return True

    async def end_forward(self, tier: str) -> None:
        self._validate_tier(tier)
        async with self._condition:
            if self._active[tier] <= 0:
                raise RuntimeError(f"no active forward to finish for tier {tier}")
            self._active[tier] -= 1
            self._condition.notify_all()

    def active_forwards(self, tier: str) -> int:
        self._validate_tier(tier)
        return self._active[tier]

    async def wait_drained(self, tier: str, timeout: float) -> bool:
        self._validate_tier(tier)
        try:
            async with self._condition:
                if self._active[tier] == 0:
                    return True
                await asyncio.wait_for(
                    self._condition.wait_for(lambda: self._active[tier] == 0),
                    timeout=max(0.0, timeout),
                )
            return True
        except asyncio.TimeoutError:
            return False

    @staticmethod
    def _validate_tier(tier: str) -> None:
        if tier not in _TIERS:
            raise ValueError(f"unknown service tier: {tier}")


def build_deployment_app(
    *,
    control: DeploymentControl,
    bearer_token: str,
    proactive_outbox: ProactiveOutbox,
    tiers,
    target_groups: Iterable[str] | Callable[[], Iterable[str]],
) -> web.Application:
    """Create authenticated HTTP handlers; the caller binds only to loopback."""

    @web.middleware
    async def authenticate(request: web.Request, handler):
        expected = bearer_token.encode("utf-8")
        supplied_header = request.headers.get("Authorization", "")
        scheme, separator, supplied_value = supplied_header.partition(" ")
        supplied = (
            supplied_value.encode("utf-8")
            if separator and scheme.lower() == "bearer"
            else b""
        )
        if not expected or not hmac.compare_digest(supplied, expected):
            return web.json_response({"ok": False, "error": "unauthorized"}, status=401)
        return await handler(request)

    async def health(request: web.Request) -> web.Response:
        status = 200 if control.ready else 503
        return web.json_response(
            {"ok": control.ready, "ready": control.ready}, status=status
        )

    async def announce(request: web.Request) -> web.Response:
        try:
            payload = await request.json()
        except (ValueError, json.JSONDecodeError):
            return web.json_response({"ok": False, "error": "invalid json"}, status=400)
        if not isinstance(payload, dict):
            return web.json_response(
                {"ok": False, "error": "expected object"}, status=400
            )
        tier = payload.get("tier")
        phase = payload.get("phase")
        notification_id = payload.get("notification_id")
        if tier not in _ANNOUNCE_TIERS or phase not in (
            *_ANNOUNCEMENTS,
            "release_note",
        ):
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

        configured_groups = (
            target_groups() if callable(target_groups) else target_groups
        )
        unique_groups = sorted(set(configured_groups))
        notice_groups = [
            group
            for group in unique_groups
            if tiers.available("update_notice", group)
            and (tier == "main" or tiers.tier_of(group) == tier)
        ]
        results = {}
        for group in notice_groups:
            try:
                proactive_outbox.enqueue_text(
                    f"deployment:{notification_id}", group, 0, text
                )
                results[group] = True
            except Exception:
                _logger.exception(
                    "部署公告持久入队失败 group=%s phase=%s", group, phase
                )
                results[group] = False
                return web.json_response({"ok": False, "results": results}, status=503)
        return web.json_response(
            {"ok": bool(results) and all(results.values()), "results": results}
        )

    async def maintenance(request: web.Request) -> web.Response:
        try:
            payload = await request.json()
        except (ValueError, json.JSONDecodeError):
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
        affected_tiers = _TIERS if tier == "main" else (tier,)
        try:
            await control.set_maintenance_many(affected_tiers, enabled)
        except OSError:
            _logger.exception("部署维护状态持久化失败 tier=%s", tier)
            return web.json_response(
                {"ok": False, "error": "state persistence failed"}, status=500
            )
        return web.json_response(
            {
                "ok": True,
                "tier": tier,
                "enabled": enabled,
                "tiers": {item: control.is_maintained(item) for item in affected_tiers},
            }
        )

    async def drained(request: web.Request) -> web.Response:
        tier = request.query.get("tier", "")
        if tier not in (*_TIERS, "main"):
            return web.json_response({"ok": False, "error": "invalid tier"}, status=400)
        affected_tiers = _TIERS if tier == "main" else (tier,)
        active = sum(control.active_forwards(item) for item in affected_tiers)
        return web.json_response(
            {"ok": True, "tier": tier, "drained": active == 0, "active": active}
        )

    app = web.Application(middlewares=[authenticate])
    app.router.add_get("/health", health)
    app.router.add_post("/deployment/announce", announce)
    app.router.add_post("/deployment/maintenance", maintenance)
    app.router.add_get("/deployment/drained", drained)
    return app
