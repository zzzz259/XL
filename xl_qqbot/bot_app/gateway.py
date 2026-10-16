"""Gateway event envelope and botpy raw-event adaptation."""

from __future__ import annotations

import asyncio
import base64
import hmac
import logging
import os
import re
import tempfile
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable

import aiohttp
import botpy
from aiohttp import web
from botpy.connection import ConnectionState

from .config import Config, require_gateway_credentials
from .deploy_control import DeploymentControl
from .groups import GroupStore, learn_group_from_event
from .sender import QQSender
from .tiers import GroupTier
from .updater import read_update_status

_logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class GatewayEvent:
    event_type: str
    received_at: str
    data: dict
    instance_id: str | None = None
    tier: str | None = None
    muted: bool = False

    def to_payload(self) -> dict:
        """Serialize the current envelope while retaining the legacy worker key."""
        payload = {
            "event_type": self.event_type,
            "type": self.event_type,
            "received_at": self.received_at,
            "data": self.data,
        }
        if self.instance_id is not None:
            payload["instance_id"] = self.instance_id
        if self.tier is not None:
            payload["tier"] = self.tier
            payload["muted"] = self.muted
        elif self.muted:
            payload["muted"] = True
        return payload


def create_gateway_event(
    event_type: str,
    data: object,
    tiers: GroupTier,
    *,
    muted: bool,
    received_at: str,
) -> GatewayEvent | None:
    """Create a routable envelope without interpreting business event fields."""
    if not isinstance(event_type, str) or not event_type.strip():
        return None
    if not isinstance(data, dict) or not data.get("group_openid"):
        return None
    return GatewayEvent(
        event_type=event_type,
        received_at=received_at,
        data=data,
        tier=tiers.tier_of(data["group_openid"]),
        muted=bool(muted),
    )


def create_instance_gateway_event(
    event_type: str,
    data: object,
    group_instances: dict[str, str],
    *,
    received_at: str,
) -> GatewayEvent | None:
    """Create an untouched raw event addressed to its sole configured instance."""
    if not isinstance(event_type, str) or not event_type.strip():
        return None
    if not isinstance(data, dict):
        return None
    group_openid = data.get("group_openid")
    if not isinstance(group_openid, str) or not group_openid:
        return None
    instance_id = group_instances.get(group_openid)
    if not instance_id:
        return None
    return GatewayEvent(
        event_type=event_type,
        received_at=received_at,
        data=data,
        instance_id=instance_id,
    )


class RawEventParserMap(dict):
    """botpy parser map that dispatches unknown event payloads as raw dicts."""

    def __init__(self, known_parsers: dict, dispatch_raw: Callable[[str, dict], None]):
        super().__init__(known_parsers)
        self._dispatch_raw = dispatch_raw

    def __missing__(self, event_type: str):
        def dispatch(payload):
            data = payload.get("d", {}) if isinstance(payload, dict) else {}
            self._dispatch_raw(event_type, data)

        return dispatch


class GatewayControlClient:
    """Authenticated deployment-service client for the Gateway control API."""

    def __init__(
        self,
        base_url: str,
        *,
        bearer_token: str,
        timeout_seconds: float = 5,
    ):
        if not isinstance(base_url, str) or not base_url.startswith(
            "http://127.0.0.1:"
        ):
            raise ValueError("Gateway control API must use a loopback HTTP URL")
        if not isinstance(bearer_token, str) or len(bearer_token) < 32:
            raise ValueError("Gateway control token must be at least 32 characters")
        if timeout_seconds <= 0:
            raise ValueError("Gateway control timeout must be positive")
        self.base_url = base_url.rstrip("/")
        self.bearer_token = bearer_token
        self._timeout = float(timeout_seconds)
        self._session: aiohttp.ClientSession | None = None

    async def start(self) -> None:
        if self._session is None or self._session.closed:
            self._session = aiohttp.ClientSession(
                timeout=aiohttp.ClientTimeout(total=self._timeout)
            )

    async def close(self) -> None:
        if self._session is not None and not self._session.closed:
            await self._session.close()

    async def health(self) -> bool:
        try:
            result = await self._request("GET", "/gateway/health")
        except RuntimeError:
            return False
        return result.get("ready") is True

    async def set_maintenance(self, tier: str, enabled: bool) -> dict:
        if not isinstance(tier, str) or not re.fullmatch(r"[a-z0-9][a-z0-9_-]{0,63}", tier):
            raise ValueError("Gateway instance_id is invalid")
        if not isinstance(enabled, bool):
            raise TypeError("Gateway maintenance state must be a boolean")
        return await self._request(
            "POST",
            "/gateway/maintenance",
            {"instance_id": tier, "enabled": enabled},
            instance_id=tier,
        )

    async def drained(self, tier: str) -> dict:
        if not isinstance(tier, str) or not re.fullmatch(r"[a-z0-9][a-z0-9_-]{0,63}", tier):
            raise ValueError("Gateway instance_id is invalid")
        return await self._request(
            "GET", f"/gateway/drained?instance_id={tier}", instance_id=tier
        )

    async def _request(
        self,
        method: str,
        path: str,
        payload: dict | None = None,
        *,
        instance_id: str | None = None,
    ) -> dict:
        if self._session is None or self._session.closed:
            raise RuntimeError("Gateway control client is not started")
        headers = {"Authorization": f"Bearer {self.bearer_token}"}
        if instance_id is not None:
            headers["X-Instance-ID"] = instance_id
        try:
            async with self._session.request(
                method,
                f"{self.base_url}{path}",
                json=payload,
                headers=headers,
            ) as response:
                body = await response.json()
        except (aiohttp.ClientError, asyncio.TimeoutError, ValueError) as exc:
            raise RuntimeError("Gateway control API request failed") from exc
        if response.status != 200 or not isinstance(body, dict) or body.get("ok") is not True:
            raise RuntimeError("Gateway control API rejected the request")
        return body


class GatewayTransportClient:
    """Instance-side client for the single QQ outbound transport."""

    def __init__(self, base_url: str, *, bearer_token: str, instance_id: str):
        if not isinstance(base_url, str) or not base_url.startswith("http://127.0.0.1:"):
            raise ValueError("Gateway transport must use a loopback HTTP URL")
        if not isinstance(bearer_token, str) or len(bearer_token) < 32:
            raise ValueError("Gateway transport token must be at least 32 characters")
        if not isinstance(instance_id, str) or not instance_id:
            raise ValueError("Gateway transport instance_id is required")
        self.base_url = base_url.rstrip("/")
        self.bearer_token = bearer_token
        self.instance_id = instance_id
        self._session: aiohttp.ClientSession | None = None

    async def start(self) -> None:
        if self._session is None or self._session.closed:
            self._session = aiohttp.ClientSession(
                timeout=aiohttp.ClientTimeout(total=120),
                headers={
                    "Authorization": f"Bearer {self.bearer_token}",
                    "X-Instance-ID": self.instance_id,
                },
            )

    async def close(self) -> None:
        if self._session is not None and not self._session.closed:
            await self._session.close()

    async def _send(self, payload: dict) -> bool:
        if self._session is None or self._session.closed:
            raise RuntimeError("Gateway transport client is not started")
        payload = {**payload, "instance_id": self.instance_id}
        try:
            async with self._session.post(
                f"{self.base_url}/gateway/messages", json=payload
            ) as response:
                body = await response.json()
        except (aiohttp.ClientError, asyncio.TimeoutError, ValueError) as error:
            raise RuntimeError("Gateway transport request failed") from error
        return response.status == 200 and isinstance(body, dict) and body.get("sent") is True

    async def send_text(self, group_openid: str, text: str, reply_to: str = "") -> bool:
        return await self._send({
            "recipient": group_openid,
            "kind": "text",
            "text": text,
            "reply_to": reply_to,
        })

    async def send_image(
        self, file_path: str, content: str, group_openids: list[str], reply_to: str = ""
    ) -> dict[str, bool]:
        return await self._send_image(file_path, content, group_openids, reply_to, False)

    async def send_schedule_image(
        self, file_path: str, content: str, group_openids: list[str], reply_to: str = ""
    ) -> dict[str, bool]:
        return await self._send_image(file_path, content, group_openids, reply_to, True)

    async def _send_image(self, file_path, content, groups, reply_to, force_multipart):
        path = Path(file_path)
        data = path.read_bytes()
        if len(data) > 20 * 1024 * 1024:
            raise ValueError("image payload exceeds the 20 MiB transport limit")
        encoded = base64.b64encode(data).decode("ascii")
        results = {}
        for group in groups:
            results[group] = await self._send({
                "recipient": group,
                "kind": "image",
                "text": content,
                "reply_to": reply_to,
                "media_base64": encoded,
                "media_name": path.name,
                "force_multipart": bool(force_multipart),
            })
        return results


def gateway_transport_for_config(config: Config, instance_id: str | None = None):
    instance_id = instance_id or config.instance.instance_id
    return GatewayTransportClient(
        f"http://127.0.0.1:{config.gateway.control_port}",
        bearer_token=config.gateway.instance_tokens.get(
            instance_id, config.gateway.control_token
        ),
        instance_id=instance_id,
    )


def install_raw_parsers() -> None:
    """Keep current group adapters and forward unknown Gateway events as raw dicts."""
    def parse_group_message_create(self, payload):
        self._dispatch("group_message_create", payload.get("d", {}))

    def parse_group_at_message_create(self, payload):
        self._dispatch("group_at_message_create", payload.get("d", {}))

    def parse_group_add_robot(self, payload):
        self._dispatch("group_add_robot", payload.get("d", {}))

    ConnectionState.parse_group_message_create = parse_group_message_create
    ConnectionState.parse_group_at_message_create = parse_group_at_message_create
    ConnectionState.parse_group_add_robot = parse_group_add_robot

    if getattr(ConnectionState, "_xl_raw_event_fallback_installed", False):
        return
    original_init = ConnectionState.__init__

    def init_with_raw_event_fallback(self, dispatch, api):
        original_init(self, dispatch, api)
        self.parsers = RawEventParserMap(
            self.parsers,
            lambda event_type, data: self._dispatch(
                "raw_gateway_event", event_type, data
            ),
        )

    ConnectionState.__init__ = init_with_raw_event_fallback
    ConnectionState._xl_raw_event_fallback_installed = True


async def run_gateway_client(
    make_client,
    appid: str,
    secret: str,
    stop_event: asyncio.Event,
    client_name: str = "Gateway client",
    readiness_control: DeploymentControl | None = None,
) -> None:
    """Recreate botpy's Gateway client after disconnects with bounded backoff."""
    backoff = 5
    while not stop_event.is_set():
        client = make_client()
        try:
            coro = await client.start(appid=appid, secret=secret, ret_coro=True)
            if coro:
                await coro
        except asyncio.CancelledError:
            raise
        except Exception:
            _logger.exception("%s connection error", client_name)
        finally:
            if readiness_control is not None:
                readiness_control.ready = False
        if stop_event.is_set():
            break
        _logger.warning("%s disconnected; reconnect in %d seconds", client_name, backoff)
        try:
            await asyncio.wait_for(stop_event.wait(), timeout=backoff)
        except asyncio.TimeoutError:
            pass
        backoff = min(backoff * 2, 300)


class InstanceForwarder:
    """Forward raw Gateway envelopes to the configured Bot instance."""

    def __init__(
        self,
        ports: dict[str, int],
        timeout: float = 8.0,
        deployment_control: DeploymentControl | None = None,
    ):
        self._ports = dict(ports)
        self._timeout = timeout
        self._deployment_control = deployment_control
        self._session: aiohttp.ClientSession | None = None

    async def _ensure_session(self) -> None:
        if self._session is None or self._session.closed:
            self._session = aiohttp.ClientSession(
                timeout=aiohttp.ClientTimeout(total=self._timeout)
            )

    def port_of(self, instance_id: str) -> int | None:
        return self._ports.get(instance_id)

    async def forward(self, instance_id: str, payload: dict) -> bool:
        port = self.port_of(instance_id)
        event_type = payload.get("event_type", payload.get("type"))
        if port is None:
            _logger.error("instance %s has no worker port; drop event %r", instance_id, event_type)
            return False
        if (
            self._deployment_control is not None
            and not await self._deployment_control.begin_forward(instance_id)
        ):
            _logger.info("instance %s is in maintenance; skip forwarding", instance_id)
            return False
        try:
            await self._ensure_session()
            async with self._session.post(
                f"http://127.0.0.1:{port}/event", json=payload
            ) as response:
                if response.status != 200:
                    _logger.error(
                        "worker returned HTTP %s for instance=%s event=%s",
                        response.status,
                        instance_id,
                        event_type,
                    )
                    return False
                return True
        except (aiohttp.ClientError, asyncio.TimeoutError) as error:
            _logger.error("worker forward failed instance=%s error=%s", instance_id, error)
            return False
        finally:
            if self._deployment_control is not None:
                await self._deployment_control.end_forward(instance_id)

    async def close(self) -> None:
        if self._session is not None and not self._session.closed:
            await self._session.close()


TierForwarder = InstanceForwarder


def worker_ports(config: Config) -> dict[str, int]:
    """Return explicit instance endpoints, with legacy tier aliases as fallback."""
    explicit_ports = getattr(config.router, "instance_ports", {})
    if explicit_ports:
        return dict(explicit_ports)
    return {
        "debug": config.router.test_port,
        "test": config.router.test_port,
        "production": config.router.production_port,
    }


class RouterClient(botpy.Client):
    """The single QQ Gateway connection and raw event transport."""

    def __init__(
        self,
        group_store: GroupStore,
        tiers: GroupTier,
        forwarder: TierForwarder,
        update_status_path: Path,
        deployment_control: DeploymentControl,
        group_instances: dict[str, str] | None = None,
    ):
        super().__init__(intents=botpy.Intents(public_messages=True))
        self._group_store = group_store
        self._tiers = tiers
        self._forwarder = forwarder
        self._update_status_path = update_status_path
        self._deployment_control = deployment_control
        self._group_instances = dict(group_instances) if group_instances is not None else None

    async def on_ready(self):
        _logger.info("Gateway ready")
        self._deployment_control.ready = True

    async def on_group_message_create(self, data: dict):
        await self._route("group_message", data)

    async def on_group_at_message_create(self, data: dict):
        await self._route("group_at", data)

    async def on_group_add_robot(self, data: dict):
        await self._route("group_add_robot", data)

    async def on_raw_gateway_event(self, event_type: str, data: dict):
        await self._route(event_type, data)

    async def _route(self, event_type: str, data: dict) -> None:
        if not isinstance(data, dict):
            _logger.warning("Gateway event data is not an object: %r", data)
            return
        group_openid = data.get("group_openid")
        if not group_openid:
            _logger.warning("Gateway event lacks group_openid: %s", event_type)
            return
        received_at = datetime.now(timezone.utc).isoformat()
        group_instances = getattr(self, "_group_instances", None)
        if group_instances is not None:
            event = create_instance_gateway_event(
                event_type, data, group_instances, received_at=received_at
            )
        else:
            learn_group_from_event(data, self._group_store)
            event = create_gateway_event(
                event_type,
                data,
                self._tiers,
                muted=self._muted_now(),
                received_at=received_at,
            )
        if event is None:
            _logger.warning("Gateway event could not form envelope: %s", event_type)
            return
        _logger.info(
            "forward Gateway event type=%s instance=%s group=%s",
            event.event_type,
            event.instance_id or event.tier,
            group_openid,
        )
        await self._forwarder.forward(event.instance_id or event.tier, event.to_payload())

    def _muted_now(self) -> bool:
        status = read_update_status(self._update_status_path)
        return bool(status and status.updating)


def build_gateway_control_app(
    control: DeploymentControl, *, bearer_token: str, sender=None,
    media_dir: Path | None = None, instance_tokens: dict[str, str] | None = None,
    group_instances: dict[str, str] | None = None,
) -> web.Application:
    """Internal authenticated health/maintenance/drain API for the Gateway."""
    if not isinstance(bearer_token, str) or len(bearer_token) < 32:
        raise ValueError("Gateway control token must be at least 32 characters")
    instance_tokens = dict(instance_tokens or {})
    if group_instances is not None:
        group_instances = dict(group_instances)
        routed_instances = set(group_instances.values())
        if not routed_instances.issubset(instance_tokens):
            raise ValueError("explicit group routes require per-instance tokens")
        routed_tokens = [instance_tokens[instance_id] for instance_id in routed_instances]
        if len(set(routed_tokens)) != len(routed_tokens):
            raise ValueError("explicit group routes require distinct per-instance tokens")
        if any(token == bearer_token for token in routed_tokens):
            raise ValueError("per-instance tokens must be different from the control token")

    @web.middleware
    async def authenticate(request: web.Request, handler):
        scheme, separator, value = request.headers.get("Authorization", "").partition(" ")
        supplied = value.encode() if separator and scheme.lower() == "bearer" else b""
        instance_id = request.headers.get("X-Instance-ID", "")
        expected_token = (
            instance_tokens.get(instance_id, bearer_token)
            if request.path == "/gateway/messages"
            else bearer_token
        )
        if (
            request.path == "/gateway/messages"
            and instance_tokens
            and instance_id not in instance_tokens
        ) or not hmac.compare_digest(supplied, expected_token.encode()):
            return web.json_response({"ok": False, "error": "unauthorized"}, status=401)
        return await handler(request)

    async def health(_request: web.Request) -> web.Response:
        ready = control.ready
        return web.json_response(
            {"ok": ready, "ready": ready}, status=200 if ready else 503
        )

    async def maintenance(request: web.Request) -> web.Response:
        try:
            payload = await request.json()
        except (ValueError, web.HTTPException):
            return web.json_response({"ok": False, "error": "invalid json"}, status=400)
        if not isinstance(payload, dict):
            return web.json_response({"ok": False, "error": "expected object"}, status=400)
        instance_id = payload.get("instance_id")
        authenticated_instance = request.headers.get("X-Instance-ID", "")
        if (instance_tokens or group_instances is not None) and instance_id != authenticated_instance:
            return web.json_response({"ok": False, "error": "instance identity mismatch"}, status=403)
        legacy_tier = payload.get("tier")
        enabled = payload.get("enabled")
        if instance_id in control.scope_ids:
            scopes = (instance_id,)
        elif instance_id == "main" and control.scope_ids == ("debug", "test", "production"):
            scopes = control.scope_ids
        elif legacy_tier in ("debug", "test", "production"):
            scopes = (legacy_tier,)
        elif legacy_tier == "main" and control.scope_ids == ("debug", "test", "production"):
            scopes = control.scope_ids
        else:
            scopes = ()
        if not scopes or any(scope not in control.scope_ids for scope in scopes) or not isinstance(enabled, bool):
            return web.json_response({"ok": False, "error": "invalid instance or enabled"}, status=400)
        try:
            await control.set_maintenance_many(scopes, enabled)
        except OSError:
            _logger.exception("Gateway maintenance persistence failed instances=%s", scopes)
            return web.json_response({"ok": False, "error": "state persistence failed"}, status=500)
        return web.json_response(
            {
                "ok": True,
                "instance_ids": scopes,
                "enabled": enabled,
                "instances": {item: control.is_maintained(item) for item in scopes},
                "tiers": {item: control.is_maintained(item) for item in scopes}
                if control._legacy_tier_state
                else {},
            }
        )

    async def drained(request: web.Request) -> web.Response:
        instance_id = request.query.get("instance_id", "")
        if instance_id in control.scope_ids:
            scopes = (instance_id,)
        elif instance_id == "main" and control.scope_ids == ("debug", "test", "production"):
            scopes = control.scope_ids
        else:
            legacy_tier = request.query.get("tier", "")
            scopes = (legacy_tier,) if legacy_tier in control.scope_ids else ()
            if legacy_tier == "main" and control.scope_ids == ("debug", "test", "production"):
                scopes = control.scope_ids
        if not scopes or any(scope not in control.scope_ids for scope in scopes):
            return web.json_response({"ok": False, "error": "invalid instance"}, status=400)
        active = sum(control.active_forwards(item) for item in scopes)
        return web.json_response(
            {"ok": True, "instance_ids": scopes, "drained": active == 0, "active": active}
        )

    async def send_message(request: web.Request) -> web.Response:
        if sender is None:
            return web.json_response({"ok": False, "error": "transport unavailable"}, status=503)
        try:
            payload = await request.json()
        except (ValueError, web.HTTPException):
            return web.json_response({"ok": False, "error": "invalid json"}, status=400)
        if not isinstance(payload, dict):
            return web.json_response({"ok": False, "error": "expected object"}, status=400)
        instance_id = payload.get("instance_id")
        recipient = payload.get("recipient")
        kind = payload.get("kind")
        text = payload.get("text", "")
        reply_to = payload.get("reply_to", "")
        if instance_id not in control.scope_ids:
            return web.json_response({"ok": False, "error": "unknown instance"}, status=403)
        authenticated_instance = request.headers.get("X-Instance-ID", "")
        if (instance_tokens or group_instances is not None) and instance_id != authenticated_instance:
            return web.json_response({"ok": False, "error": "instance identity mismatch"}, status=403)
        if not isinstance(recipient, str) or not recipient:
            return web.json_response({"ok": False, "error": "invalid recipient"}, status=400)
        if group_instances is not None and group_instances.get(recipient) != instance_id:
            return web.json_response({"ok": False, "error": "recipient is not owned by instance"}, status=403)
        if not isinstance(text, str) or len(text) > 4000:
            return web.json_response({"ok": False, "error": "invalid text"}, status=400)
        if not isinstance(reply_to, str) or len(reply_to) > 200:
            return web.json_response({"ok": False, "error": "invalid reply_to"}, status=400)
        if kind == "text":
            if not text.strip():
                return web.json_response({"ok": False, "error": "empty text"}, status=400)
            try:
                sent = await sender.send_text(recipient, text, reply_to)
            except Exception:
                _logger.exception("Gateway text transport failed instance=%s", instance_id)
                return web.json_response({"ok": False, "error": "transport failed"}, status=503)
            return web.json_response({"ok": True, "sent": sent is True})
        if kind != "image":
            return web.json_response({"ok": False, "error": "unsupported message kind"}, status=400)
        encoded = payload.get("media_base64")
        media_name = payload.get("media_name")
        if not isinstance(encoded, str) or len(encoded) > 28_000_000:
            return web.json_response({"ok": False, "error": "invalid media"}, status=400)
        try:
            media = base64.b64decode(encoded, validate=True)
        except (ValueError, base64.binascii.Error):
            return web.json_response({"ok": False, "error": "invalid media"}, status=400)
        if not media or len(media) > 20 * 1024 * 1024:
            return web.json_response({"ok": False, "error": "media size out of range"}, status=400)
        safe_name = Path(media_name if isinstance(media_name, str) else "image.bin").name
        directory = media_dir
        if directory is None:
            return web.json_response({"ok": False, "error": "media storage unavailable"}, status=503)
        directory.mkdir(parents=True, exist_ok=True)
        fd, media_path = tempfile.mkstemp(prefix="gateway-", suffix="-" + safe_name, dir=directory)
        try:
            with os.fdopen(fd, "wb") as handle:
                handle.write(media)
            try:
                if payload.get("force_multipart") is True:
                    result = await sender.send_schedule_image(media_path, text, [recipient], reply_to)
                else:
                    result = await sender.send_image(media_path, text, [recipient], reply_to)
            except Exception:
                _logger.exception("Gateway image transport failed instance=%s", instance_id)
                return web.json_response({"ok": False, "error": "transport failed"}, status=503)
            sent = isinstance(result, dict) and result.get(recipient) is True
            return web.json_response({"ok": True, "sent": sent})
        finally:
            try:
                Path(media_path).unlink()
            except OSError:
                _logger.warning("Gateway temporary media cleanup failed")

    app = web.Application(middlewares=[authenticate], client_max_size=30 * 1024 * 1024)
    app.router.add_get("/gateway/health", health)
    app.router.add_post("/gateway/maintenance", maintenance)
    app.router.add_get("/gateway/drained", drained)
    app.router.add_post("/gateway/messages", send_message)
    if sender is not None:
        async def start_sender(_app):
            await sender.start()

        async def close_sender(_app):
            await sender.close()

        app.on_startup.append(start_sender)
        app.on_cleanup.append(close_sender)
    return app


class GatewayRuntime:
    """Run only the single QQ Gateway client and its minimal control endpoint."""

    def __init__(self, config: Config, *, client_factory=None, sender_factory=None):
        require_gateway_credentials(config)
        self.config = config
        self.tiers = GroupTier(config.groups)
        maintenance_path = Path(config.watch.data_dir) / "maintenance.json"
        if config.router.instance_ports:
            self.control = DeploymentControl(
                maintenance_path,
                instance_ids=tuple(config.router.instance_ports),
            )
        else:
            self.control = DeploymentControl(maintenance_path)
        self.forwarder = TierForwarder(
            worker_ports(config),
            timeout=config.router.forward_timeout,
            deployment_control=self.control,
        )
        self.update_status_path = Path(config.watch.outbox_dir).parent / "update_status.json"
        self.stop_event = asyncio.Event()
        self.client_factory = client_factory or self._make_client
        self.sender = (sender_factory or QQSender)(config)
        self._gateway_task: asyncio.Task | None = None
        self._deployment_runner: web.AppRunner | None = None

    def _make_client(self):
        return RouterClient(
            GroupStore(self.config.watch.data_dir),
            self.tiers,
            self.forwarder,
            self.update_status_path,
            self.control,
            group_instances=(
                self.config.router.group_instances
                if self.config.router.group_routing_configured or self.config.router.instance_ports
                else None
            ),
        )

    async def run(self) -> None:
        app = build_gateway_control_app(
            self.control,
            bearer_token=self.config.gateway.control_token,
            sender=self.sender,
            media_dir=Path(self.config.watch.data_dir) / "gateway-media",
            instance_tokens=self.config.gateway.instance_tokens,
            group_instances=(
                self.config.router.group_instances
                if self.config.router.group_routing_configured or self.config.router.instance_ports
                else None
            ),
        )
        self._deployment_runner = web.AppRunner(app)
        await self._deployment_runner.setup()
        site = web.TCPSite(
            self._deployment_runner,
            "127.0.0.1",
            self.config.gateway.control_port,
        )
        await site.start()
        self._gateway_task = asyncio.create_task(
            run_gateway_client(
                self.client_factory,
                self.config.bot.appid,
                self.config.bot.secret,
                self.stop_event,
                client_name="QQ Gateway",
                readiness_control=self.control,
            )
        )
        try:
            await self._gateway_task
        except asyncio.CancelledError:
            if not self.stop_event.is_set():
                raise
        finally:
            self.control.ready = False
            if self._deployment_runner is not None:
                await self._deployment_runner.cleanup()
            await self.forwarder.close()

    def stop(self) -> None:
        self.control.ready = False
        self.stop_event.set()
        if self._gateway_task is not None and not self._gateway_task.done():
            self._gateway_task.cancel()
