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
    """Persistent per-instance maintenance state and in-flight accounting."""

    def __init__(
        self, maintenance_path: str | Path, *, instance_ids: Iterable[str] | None = None
    ):
        self.path = Path(maintenance_path)
        self._condition = asyncio.Condition()
        self._legacy_tier_state = instance_ids is None
        self._scopes = tuple(instance_ids) if instance_ids is not None else _TIERS
        if not self._scopes or len(set(self._scopes)) != len(self._scopes):
            raise ValueError("maintenance scope IDs must be nonempty and unique")
        self._maintenance = {scope: False for scope in self._scopes}
        self._active = {scope: 0 for scope in self._scopes}
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
            state_key = "tiers" if self._legacy_tier_state else "instances"
            scopes = payload.get(state_key)
            if not isinstance(scopes, dict):
                raise TypeError(f"{state_key!r} must be an object")
            if set(scopes) != set(self._scopes):
                missing = sorted(set(self._scopes) - set(scopes))
                unknown = sorted(set(scopes) - set(self._scopes))
                raise ValueError(
                    f"scope keys must be exactly {self._scopes}; "
                    f"missing={missing}, unknown={unknown}"
                )
            if any(not isinstance(value, bool) for value in scopes.values()):
                raise ValueError("every scope state must be a boolean")
        except (json.JSONDecodeError, ValueError, TypeError) as exc:
            raise RuntimeError(
                f"invalid maintenance state at {self.path}: {exc}"
            ) from exc

        # Do not expose partially validated state if any persisted tier is invalid.
        self._maintenance = {scope: scopes[scope] for scope in self._scopes}

    def _save(self, scopes: dict[str, bool]) -> None:
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
                state_key = "tiers" if self._legacy_tier_state else "instances"
                json.dump({state_key: scopes}, temporary, sort_keys=True)
                temporary.flush()
                os.fsync(temporary.fileno())
            os.replace(temporary_path, self.path)
        finally:
            if temporary_path is not None and temporary_path.exists():
                temporary_path.unlink()

    def is_maintained(self, instance_id: str) -> bool:
        self._validate_scope(instance_id)
        return self._maintenance[instance_id]

    async def set_maintenance(self, instance_id: str, enabled: bool) -> None:
        await self.set_maintenance_many((instance_id,), enabled)

    async def set_maintenance_many(self, instance_ids: Iterable[str], enabled: bool) -> None:
        instance_ids = tuple(dict.fromkeys(instance_ids))
        if not instance_ids:
            raise ValueError("at least one maintenance scope is required")
        for instance_id in instance_ids:
            self._validate_scope(instance_id)
        if not isinstance(enabled, bool):
            raise TypeError("maintenance state must be a boolean")
        async with self._condition:
            updated = dict(self._maintenance)
            updated.update({instance_id: enabled for instance_id in instance_ids})
            self._save(updated)
            self._maintenance = updated
            self._condition.notify_all()

    async def begin_forward(self, instance_id: str) -> bool:
        self._validate_scope(instance_id)
        async with self._condition:
            if self._maintenance[instance_id]:
                return False
            self._active[instance_id] += 1
            return True

    async def end_forward(self, instance_id: str) -> None:
        self._validate_scope(instance_id)
        async with self._condition:
            if self._active[instance_id] <= 0:
                raise RuntimeError(f"no active forward to finish for instance {instance_id}")
            self._active[instance_id] -= 1
            self._condition.notify_all()

    def active_forwards(self, instance_id: str) -> int:
        self._validate_scope(instance_id)
        return self._active[instance_id]

    async def wait_drained(self, instance_id: str, timeout: float) -> bool:
        self._validate_scope(instance_id)
        try:
            async with self._condition:
                if self._active[instance_id] == 0:
                    return True
                await asyncio.wait_for(
                    self._condition.wait_for(lambda: self._active[instance_id] == 0),
                    timeout=max(0.0, timeout),
                )
            return True
        except asyncio.TimeoutError:
            return False

    def _validate_scope(self, instance_id: str) -> None:
        if instance_id not in self._scopes:
            raise ValueError(f"unknown maintenance instance: {instance_id}")

    @property
    def scope_ids(self) -> tuple[str, ...]:
        return self._scopes

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
            if tiers.tier_of(group) == ("production" if tier == "main" else tier)
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
