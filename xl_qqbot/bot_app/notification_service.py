"""Tier-scoped, durable notification intake and delivery service."""

from __future__ import annotations

import argparse
import asyncio
import hmac
import logging
import re
from pathlib import Path
from urllib.parse import quote

import aiohttp
from aiohttp import web

from .config import Config, load_config
from .groups import GroupStore
from .proactive_dispatcher import ProactiveDispatcher
from .proactive_outbox import ProactiveOutbox
from .sender import QQSender
from .tiers import GroupTier

_logger = logging.getLogger(__name__)
_SERVICE_TIERS = {
    "test": frozenset({"debug", "test"}),
    "production": frozenset({"main", "production"}),
}
_EVENT_KEY_RE = re.compile(r"[A-Za-z0-9._:-]{1,200}")
_STOP_KEY = web.AppKey("notification_stop_event", asyncio.Event)
_DISPATCH_TASK_KEY = web.AppKey("notification_dispatch_task", asyncio.Task)


class NotificationDeliveryError(RuntimeError):
    """The notification API did not durably accept a producer request."""


class NotificationClient:
    """Authenticated asynchronous producer client for a tier notifier."""

    def __init__(
        self,
        base_url: str,
        *,
        bearer_token: str,
        timeout_seconds: float = 10,
    ):
        if not isinstance(base_url, str) or not base_url.startswith("http://127.0.0.1:"):
            raise ValueError("notification API must use a loopback HTTP URL")
        if not isinstance(bearer_token, str) or len(bearer_token) < 32:
            raise ValueError("notification bearer token must be at least 32 characters")
        if timeout_seconds <= 0:
            raise ValueError("notification client timeout must be positive")
        self.base_url = base_url.rstrip("/")
        self.bearer_token = bearer_token
        self.timeout_seconds = float(timeout_seconds)
        self._session: aiohttp.ClientSession | None = None

    async def start(self) -> None:
        if self._session is None or self._session.closed:
            self._session = aiohttp.ClientSession(
                timeout=aiohttp.ClientTimeout(total=self.timeout_seconds)
            )

    async def close(self) -> None:
        if self._session is not None and not self._session.closed:
            await self._session.close()

    async def health(self) -> bool:
        if self._session is None or self._session.closed:
            return False
        try:
            async with self._session.get(f"{self.base_url}/health") as response:
                body = await response.json()
        except (aiohttp.ClientError, asyncio.TimeoutError, ValueError):
            return False
        return response.status == 200 and isinstance(body, dict) and body.get("ok") is True

    async def enqueue(self, *, event_key: str, tier: str, messages: list[dict]) -> list[dict]:
        if self._session is None or self._session.closed:
            raise RuntimeError("notification client is not started")
        try:
            async with self._session.post(
                f"{self.base_url}/notifications",
                json={"event_key": event_key, "tier": tier, "messages": messages},
                headers={"Authorization": f"Bearer {self.bearer_token}"},
            ) as response:
                body = await response.json()
        except (aiohttp.ClientError, asyncio.TimeoutError, ValueError) as exc:
            raise NotificationDeliveryError("notification API request failed") from exc
        queued = body.get("queued") if isinstance(body, dict) else None
        if response.status != 200 or not isinstance(queued, list) or len(queued) != len(messages):
            raise NotificationDeliveryError(
                f"notification API rejected durable enqueue (HTTP {response.status})"
            )
        return queued

    async def wait_for_delivery(
        self,
        event_key: str,
        *,
        expected_total: int,
        timeout_seconds: float,
        poll_interval_seconds: float = 0.25,
    ) -> bool:
        if self._session is None or self._session.closed:
            raise RuntimeError("notification client is not started")
        if (
            isinstance(expected_total, bool)
            or not isinstance(expected_total, int)
            or expected_total <= 0
            or timeout_seconds <= 0
            or poll_interval_seconds <= 0
        ):
            raise ValueError("delivery count and timeouts must be positive")
        url = f"{self.base_url}/notifications/{quote(event_key, safe='')}"
        deadline = asyncio.get_running_loop().time() + timeout_seconds
        while True:
            remaining = deadline - asyncio.get_running_loop().time()
            if remaining <= 0:
                return False
            request_timeout = aiohttp.ClientTimeout(total=min(5.0, remaining))
            try:
                async with self._session.get(
                    url,
                    timeout=request_timeout,
                    headers={"Authorization": f"Bearer {self.bearer_token}"},
                ) as response:
                    body = await response.json()
            except (aiohttp.ClientError, asyncio.TimeoutError, ValueError) as exc:
                raise NotificationDeliveryError(
                    "notification delivery-status request failed"
                ) from exc
            status = body if isinstance(body, dict) else {}
            if response.status not in (200, 404) or status.get("ok") is not True:
                raise NotificationDeliveryError(
                    f"notification delivery-status rejected (HTTP {response.status})"
                )
            total = status.get("total", 0)
            sent = status.get("sent", 0)
            pending = status.get("pending", 0)
            if (
                total == expected_total
                and sent == expected_total
                and pending == 0
            ):
                return True
            if total > expected_total:
                raise NotificationDeliveryError(
                    "notification delivery count exceeded the expected total"
                )
            remaining = deadline - asyncio.get_running_loop().time()
            if remaining <= 0:
                return False
            await asyncio.sleep(min(poll_interval_seconds, remaining))


class NotificationService:
    """Validate tier scope and persist every item before acknowledging intake."""

    def __init__(self, *, tier: str, allowed_groups, outbox: ProactiveOutbox):
        if tier not in _SERVICE_TIERS:
            raise ValueError("notification service tier must be test or production")
        self.tier = tier
        self.allowed_tiers = _SERVICE_TIERS[tier]
        self.allowed_groups = frozenset(
            group for group in allowed_groups if isinstance(group, str) and group
        )
        self.outbox = outbox

    def enqueue(self, payload: object) -> tuple[int, dict]:
        if not isinstance(payload, dict):
            return 400, {"ok": False, "error": "expected object"}
        event_key = payload.get("event_key")
        tier = payload.get("tier")
        messages = payload.get("messages")
        if not isinstance(event_key, str) or _EVENT_KEY_RE.fullmatch(event_key) is None:
            return 400, {"ok": False, "error": "invalid event_key"}
        if tier not in self.allowed_tiers:
            return 403, {"ok": False, "error": "tier is outside service scope"}
        if not isinstance(messages, list) or not messages or len(messages) > 100:
            return 400, {"ok": False, "error": "messages must contain 1 to 100 items"}

        validated = []
        for item in messages:
            result = self._validate_message(item)
            if isinstance(result, tuple):
                return result
            validated.append(result)

        validated.sort(key=lambda item: (item["recipient"], item["ordinal"]))
        queued = []
        try:
            for item in validated:
                if item["kind"] == "text":
                    sequence = self.outbox.enqueue_text(
                        event_key,
                        item["recipient"],
                        item["ordinal"],
                        item["text"],
                    )
                else:
                    sequence = self.outbox.enqueue_image(
                        event_key,
                        item["recipient"],
                        item["ordinal"],
                        item["media_path"],
                        content=item["text"],
                    )
                queued.append(
                    {
                        "recipient": item["recipient"],
                        "ordinal": item["ordinal"],
                        "sequence": sequence,
                    }
                )
        except (OSError, ValueError, TypeError) as exc:
            _logger.warning("通知入队失败 event_key=%s error=%s", event_key, exc)
            return 400, {"ok": False, "error": "notification could not be queued"}
        except Exception:
            _logger.exception("通知入队异常 event_key=%s", event_key)
            return 503, {"ok": False, "error": "notification service unavailable"}
        return 200, {"ok": True, "queued": queued}

    def event_status(self, event_key: str) -> tuple[int, dict]:
        if not isinstance(event_key, str) or _EVENT_KEY_RE.fullmatch(event_key) is None:
            return 400, {"ok": False, "error": "invalid event_key"}
        status = self.outbox.event_status(event_key)
        if status["total"] == 0:
            return 404, {"ok": True, **status}
        return 200, {"ok": True, **status}

    def _validate_message(self, item: object) -> dict | tuple[int, dict]:
        if not isinstance(item, dict):
            return 400, {"ok": False, "error": "message must be an object"}
        recipient = item.get("recipient")
        if not isinstance(recipient, str) or not recipient:
            return 400, {"ok": False, "error": "invalid recipient"}
        if recipient not in self.allowed_groups:
            return 403, {"ok": False, "error": "recipient is outside service scope"}
        ordinal = item.get("ordinal")
        if isinstance(ordinal, bool) or not isinstance(ordinal, int) or ordinal < 0:
            return 400, {"ok": False, "error": "invalid ordinal"}
        kind = item.get("kind")
        text = item.get("text", "")
        if not isinstance(text, str) or len(text) > 4000:
            return 400, {"ok": False, "error": "invalid text"}
        if kind == "text":
            if not text.strip():
                return 400, {"ok": False, "error": "text message cannot be empty"}
            return {
                "recipient": recipient,
                "ordinal": ordinal,
                "kind": kind,
                "text": text,
            }
        if kind == "image":
            media_path = item.get("media_path")
            if not isinstance(media_path, str) or not media_path:
                return 400, {"ok": False, "error": "invalid media_path"}
            return {
                "recipient": recipient,
                "ordinal": ordinal,
                "kind": kind,
                "text": text,
                "media_path": media_path,
            }
        return 400, {"ok": False, "error": "unsupported message kind"}


def build_notification_app(
    service: NotificationService,
    *,
    bearer_token: str,
    sender,
) -> web.Application:
    """Create intake and delivery endpoints; caller binds to loopback only."""
    if not isinstance(bearer_token, str) or len(bearer_token) < 32:
        raise ValueError("notification bearer token must be at least 32 characters")

    @web.middleware
    async def authenticate_notifications(request: web.Request, handler):
        if request.path == "/health":
            return await handler(request)
        scheme, separator, supplied_value = request.headers.get(
            "Authorization", ""
        ).partition(" ")
        supplied = (
            supplied_value.encode("utf-8")
            if separator and scheme.lower() == "bearer"
            else b""
        )
        if not hmac.compare_digest(supplied, bearer_token.encode("utf-8")):
            return web.json_response({"ok": False, "error": "unauthorized"}, status=401)
        return await handler(request)

    async def health(request: web.Request) -> web.Response:
        dispatcher_task = request.app.get(_DISPATCH_TASK_KEY)
        ready = dispatcher_task is not None and not dispatcher_task.done()
        return web.json_response(
            {"ok": ready, "ready": ready, "tier": service.tier},
            status=200 if ready else 503,
        )

    async def enqueue(request: web.Request) -> web.Response:
        try:
            payload = await request.json()
        except (ValueError, web.HTTPException):
            return web.json_response({"ok": False, "error": "invalid json"}, status=400)
        status, response = service.enqueue(payload)
        return web.json_response(response, status=status)

    async def event_status(request: web.Request) -> web.Response:
        status, response = service.event_status(
            request.match_info.get("event_key", "")
        )
        return web.json_response(response, status=status)

    async def on_startup(app: web.Application) -> None:
        await sender.start()
        stop_event = asyncio.Event()
        dispatcher = ProactiveDispatcher(service.outbox, sender)
        app[_STOP_KEY] = stop_event
        app[_DISPATCH_TASK_KEY] = asyncio.create_task(dispatcher.run(stop_event))

    async def on_cleanup(app: web.Application) -> None:
        stop_event = app.get(_STOP_KEY)
        dispatch_task = app.get(_DISPATCH_TASK_KEY)
        if stop_event is not None:
            stop_event.set()
        if dispatch_task is not None:
            await dispatch_task
        await sender.close()

    app = web.Application(
        middlewares=[authenticate_notifications], client_max_size=1024 * 1024
    )
    app.router.add_get("/health", health)
    app.router.add_post("/notifications", enqueue)
    app.router.add_get("/notifications/{event_key}", event_status)
    app.on_startup.append(on_startup)
    app.on_cleanup.append(on_cleanup)
    return app


def allowed_notification_groups(config: Config, tier: str) -> frozenset[str]:
    """Resolve configured and learned groups, then enforce the service tier scope."""
    if tier not in _SERVICE_TIERS:
        raise ValueError("notification service tier must be test or production")
    configured_groups = set(config.target.group_openids)
    if not configured_groups:
        configured_groups.update(GroupStore(config.watch.data_dir).openids())
    configured_groups.update(config.groups.debug)
    configured_groups.update(config.groups.test)
    group_tiers = GroupTier(config.groups)
    return frozenset(
        group
        for group in configured_groups
        if group_tiers.tier_of(group) in _SERVICE_TIERS[tier]
    )


def parse_args(argv=None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="xl_qqbot tier notification service")
    parser.add_argument(
        "--tier", required=True, choices=("test", "production"),
        help="notification scope served by this process",
    )
    return parser.parse_args(argv)


def main(argv=None) -> None:
    args = parse_args(argv)
    config = load_config("config.toml")
    if len(config.notification.api_token) < 32:
        raise ValueError("[notification] api_token must be configured with 32+ characters")
    port = (
        config.notification.test_port
        if args.tier == "test"
        else config.notification.production_port
    )
    data_dir = Path(config.watch.data_dir) / "notifications" / args.tier
    outbox = ProactiveOutbox(data_dir)
    service = NotificationService(
        tier=args.tier,
        allowed_groups=allowed_notification_groups(config, args.tier),
        outbox=outbox,
    )
    app = build_notification_app(
        service,
        bearer_token=config.notification.api_token,
        sender=QQSender(config),
    )
    web.run_app(app, host="127.0.0.1", port=port)


if __name__ == "__main__":
    main()
