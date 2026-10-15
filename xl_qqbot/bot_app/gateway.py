"""Gateway event envelope and botpy raw-event adaptation."""

from __future__ import annotations

import asyncio
import hmac
import logging
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable

import aiohttp
import botpy
from aiohttp import web
from botpy.connection import ConnectionState

from .config import Config
from .deploy_control import DeploymentControl
from .groups import GroupStore, learn_group_from_event
from .tiers import GroupTier
from .updater import read_update_status

_logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class GatewayEvent:
    event_type: str
    tier: str
    received_at: str
    muted: bool
    data: dict

    def to_payload(self) -> dict:
        """Serialize the current envelope while retaining the legacy worker key."""
        return {
            "event_type": self.event_type,
            "type": self.event_type,
            "tier": self.tier,
            "received_at": self.received_at,
            "muted": self.muted,
            "data": self.data,
        }


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
        tier=tiers.tier_of(data["group_openid"]),
        received_at=received_at,
        muted=bool(muted),
        data=data,
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
        if tier not in {"debug", "test", "production", "main"}:
            raise ValueError("Gateway tier is not allowlisted")
        if not isinstance(enabled, bool):
            raise TypeError("Gateway maintenance state must be a boolean")
        return await self._request(
            "POST", "/gateway/maintenance", {"tier": tier, "enabled": enabled}
        )

    async def drained(self, tier: str) -> dict:
        if tier not in {"debug", "test", "production", "main"}:
            raise ValueError("Gateway tier is not allowlisted")
        return await self._request("GET", f"/gateway/drained?tier={tier}")

    async def _request(self, method: str, path: str, payload: dict | None = None) -> dict:
        if self._session is None or self._session.closed:
            raise RuntimeError("Gateway control client is not started")
        try:
            async with self._session.request(
                method,
                f"{self.base_url}{path}",
                json=payload,
                headers={"Authorization": f"Bearer {self.bearer_token}"},
            ) as response:
                body = await response.json()
        except (aiohttp.ClientError, asyncio.TimeoutError, ValueError) as exc:
            raise RuntimeError("Gateway control API request failed") from exc
        if response.status != 200 or not isinstance(body, dict) or body.get("ok") is not True:
            raise RuntimeError("Gateway control API rejected the request")
        return body


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


class TierForwarder:
    """Forward raw Gateway envelopes to the worker assigned to each tier."""

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

    def port_of(self, tier: str) -> int | None:
        return self._ports.get(tier)

    async def forward(self, tier: str, payload: dict) -> bool:
        port = self.port_of(tier)
        event_type = payload.get("event_type", payload.get("type"))
        if port is None:
            _logger.error("tier %s has no worker port; drop event %r", tier, event_type)
            return False
        if (
            self._deployment_control is not None
            and not await self._deployment_control.begin_forward(tier)
        ):
            _logger.info("tier %s is in maintenance; skip forwarding", tier)
            return False
        try:
            await self._ensure_session()
            async with self._session.post(
                f"http://127.0.0.1:{port}/event", json=payload
            ) as response:
                if response.status != 200:
                    _logger.error(
                        "worker returned HTTP %s for tier=%s event=%s",
                        response.status,
                        tier,
                        event_type,
                    )
                    return False
                return True
        except (aiohttp.ClientError, asyncio.TimeoutError) as error:
            _logger.error("worker forward failed tier=%s error=%s", tier, error)
            return False
        finally:
            if self._deployment_control is not None:
                await self._deployment_control.end_forward(tier)

    async def close(self) -> None:
        if self._session is not None and not self._session.closed:
            await self._session.close()


def worker_ports(config: Config) -> dict[str, int]:
    """Debug and test groups share the test worker; production stays separate."""
    return {
        "debug": config.router.test_port,
        "test": config.router.test_port,
        "production": config.router.production_port,
    }


class RouterClient(botpy.Client):
    """The single QQ Gateway connection and tier event ingress."""

    def __init__(
        self,
        group_store: GroupStore,
        tiers: GroupTier,
        forwarder: TierForwarder,
        update_status_path: Path,
        deployment_control: DeploymentControl,
    ):
        super().__init__(intents=botpy.Intents(public_messages=True))
        self._group_store = group_store
        self._tiers = tiers
        self._forwarder = forwarder
        self._update_status_path = update_status_path
        self._deployment_control = deployment_control

    async def on_ready(self):
        _logger.info("Gateway ready")
        self._deployment_control.ready = True

    async def on_group_message_create(self, data: dict):
        await self._route("group_message", data)

    async def on_group_at_message_create(self, data: dict):
        await self._route("group_at", data)

    async def on_group_add_robot(self, data: dict):
        learn_group_from_event(data, self._group_store)
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
        learn_group_from_event(data, self._group_store)
        event = create_gateway_event(
            event_type,
            data,
            self._tiers,
            muted=self._muted_now(),
            received_at=datetime.now(timezone.utc).isoformat(),
        )
        if event is None:
            _logger.warning("Gateway event could not form envelope: %s", event_type)
            return
        _logger.info(
            "forward Gateway event type=%s tier=%s group=%s",
            event.event_type,
            event.tier,
            group_openid,
        )
        await self._forwarder.forward(event.tier, event.to_payload())

    def _muted_now(self) -> bool:
        status = read_update_status(self._update_status_path)
        return bool(status and status.updating)


def build_gateway_control_app(
    control: DeploymentControl, *, bearer_token: str
) -> web.Application:
    """Internal authenticated health/maintenance/drain API for the Gateway."""
    if not isinstance(bearer_token, str) or len(bearer_token) < 32:
        raise ValueError("Gateway control token must be at least 32 characters")

    @web.middleware
    async def authenticate(request: web.Request, handler):
        scheme, separator, value = request.headers.get("Authorization", "").partition(" ")
        supplied = value.encode() if separator and scheme.lower() == "bearer" else b""
        if not hmac.compare_digest(supplied, bearer_token.encode()):
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
        tier = payload.get("tier")
        enabled = payload.get("enabled")
        if tier not in ("debug", "test", "production", "main") or not isinstance(enabled, bool):
            return web.json_response({"ok": False, "error": "invalid tier or enabled"}, status=400)
        affected = ("debug", "test", "production") if tier == "main" else (tier,)
        try:
            await control.set_maintenance_many(affected, enabled)
        except OSError:
            _logger.exception("Gateway maintenance persistence failed tier=%s", tier)
            return web.json_response({"ok": False, "error": "state persistence failed"}, status=500)
        return web.json_response(
            {
                "ok": True,
                "tier": tier,
                "enabled": enabled,
                "tiers": {item: control.is_maintained(item) for item in affected},
            }
        )

    async def drained(request: web.Request) -> web.Response:
        tier = request.query.get("tier", "")
        if tier not in ("debug", "test", "production", "main"):
            return web.json_response({"ok": False, "error": "invalid tier"}, status=400)
        affected = ("debug", "test", "production") if tier == "main" else (tier,)
        active = sum(control.active_forwards(item) for item in affected)
        return web.json_response(
            {"ok": True, "tier": tier, "drained": active == 0, "active": active}
        )

    app = web.Application(middlewares=[authenticate])
    app.router.add_get("/gateway/health", health)
    app.router.add_post("/gateway/maintenance", maintenance)
    app.router.add_get("/gateway/drained", drained)
    return app


class GatewayRuntime:
    """Run only the single QQ Gateway client and its minimal control endpoint."""

    def __init__(self, config: Config, *, client_factory=None):
        self.config = config
        self.tiers = GroupTier(config.groups)
        self.control = DeploymentControl(
            Path(config.watch.data_dir) / "maintenance.json"
        )
        self.forwarder = TierForwarder(
            worker_ports(config),
            timeout=config.router.forward_timeout,
            deployment_control=self.control,
        )
        self.update_status_path = Path(config.watch.outbox_dir).parent / "update_status.json"
        self.stop_event = asyncio.Event()
        self.client_factory = client_factory or self._make_client
        self._gateway_task: asyncio.Task | None = None
        self._deployment_runner: web.AppRunner | None = None

    def _make_client(self):
        return RouterClient(
            GroupStore(self.config.watch.data_dir),
            self.tiers,
            self.forwarder,
            self.update_status_path,
            self.control,
        )

    async def run(self) -> None:
        app = build_gateway_control_app(
            self.control, bearer_token=self.config.gateway.control_token
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
