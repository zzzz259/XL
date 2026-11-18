"""Authenticated synchronous client for the loopback router deployment API."""

from __future__ import annotations

import ipaddress
import json
import re
import time
import uuid
from collections.abc import Callable, Mapping, Sequence
from typing import Any
from urllib.parse import urlencode, urlsplit
from urllib.request import HTTPRedirectHandler, ProxyHandler, Request, build_opener

START_NOTICE = "检测到更新，正在更新bot，期间将暂停服务"
COMPLETION_NOTICE = "更新完毕"


class _NoRedirectHandler(HTTPRedirectHandler):
    def redirect_request(self, request, fp, code, message, headers, new_url):
        return None


def _open_without_redirects(request: Request, *, timeout: float):
    return build_opener(ProxyHandler({}), _NoRedirectHandler).open(request, timeout=timeout)


class RouterControlClient:
    def __init__(
        self,
        base_url: str,
        token: str,
        *,
        inventory=None,
        reply_probe_ports: Mapping[str, int] | None = None,
        transport: Callable[..., Any] | None = None,
        timeout_seconds: float = 5,
        poll_interval_seconds: float = 0.25,
    ):
        validate_loopback_url(base_url)
        if not token.strip() or timeout_seconds <= 0 or poll_interval_seconds <= 0:
            raise ValueError("router token and positive timeouts are required")
        probe_ports = {} if reply_probe_ports is None else reply_probe_ports
        enabled = {item.instance_id for item in inventory.instances if item.enabled} if inventory else set()
        if not isinstance(probe_ports, Mapping) or any(
            instance not in enabled or type(port) is not int or not 0 < port < 65536
            for instance, port in probe_ports.items()
        ):
            raise ValueError("reply_probe_ports must map enabled Bot instances to loopback ports")
        self.inventory = inventory
        self.reply_probe_ports = dict(probe_ports)
        self.base_url = base_url.rstrip("/")
        self.token = token
        self.transport = transport or _open_without_redirects
        self.timeout_seconds = timeout_seconds
        self.poll_interval_seconds = poll_interval_seconds

    def announce(
        self, scope: Sequence[str], text: str, *, notification_id: str
    ) -> dict[str, bool]:
        base_id = _notification_id(notification_id)
        if text == START_NOTICE:
            phase = "starting"
        elif text == COMPLETION_NOTICE:
            phase = "complete"
        else:
            return self.announce_release_note(scope, text, notification_id=base_id)
        results: dict[str, bool] = {}
        for tier in scope:
            safe_tier = self._scope(tier)
            payload = self._post(
                "/deployment/announce",
                {
                    self._scope_key: safe_tier,
                    "phase": phase,
                    "notification_id": f"{base_id}:{safe_tier}",
                },
            )
            _merge_results(results, payload)
        return results

    def announce_release_note(
        self, scope: Sequence[str], text: str, *, notification_id: str
    ) -> dict[str, bool]:
        base_id = _notification_id(notification_id)
        results: dict[str, bool] = {}
        for tier in scope:
            safe_tier = self._scope(tier)
            payload = self._post(
                "/deployment/announce",
                {
                    self._scope_key: safe_tier,
                    "phase": "release_note",
                    "notification_id": f"{base_id}:{safe_tier}",
                    "text": text,
                },
            )
            _merge_results(results, payload)
        return results

    def pause(self, tiers: Sequence[str]) -> None:
        for tier in tiers:
            self._post(
                "/deployment/maintenance", {self._scope_key: self._scope(tier), "enabled": True}
            )

    def resume(self, tiers: Sequence[str]) -> None:
        for tier in tiers:
            self._post(
                "/deployment/maintenance", {self._scope_key: self._scope(tier), "enabled": False}
            )

    def require_reply_path(self, scope: Sequence[str]) -> None:
        """Check live Gateway and Worker endpoints for this completion audience."""
        gateway = self.inventory.gateway
        ready = self._send(Request(
            f"http://127.0.0.1:{gateway.listen_port}/gateway/health", headers=self._headers(),
            method="GET",
        ))
        if ready.get("ready") is not True:
            raise RuntimeError("Gateway is not ready for reply probes")
        entries = {entry.instance_id: entry for entry in self.inventory.instances if entry.enabled}
        for target in scope:
            instance_id = self._scope(target)
            entry = entries[instance_id]
            port = self.reply_probe_ports.get(instance_id)
            if port is None and gateway.managed:
                port = entry.listen_ports.get("bot")
            if port is None:
                raise RuntimeError(f"reply probe route is not configured for {instance_id}")
            nonce = uuid.uuid4().hex
            data = {"instance_id": instance_id, "nonce": nonce}
            response = self._send(Request(
                f"http://127.0.0.1:{port}/event",
                data=json.dumps({"event_type": "deployment_reply_probe", "data": data}).encode("utf-8"),
                headers={"Content-Type": "application/json"}, method="POST",
            ))
            if response.get("instance_id") != instance_id or response.get("nonce") != nonce:
                raise RuntimeError(f"reply probe acknowledgement mismatched for {instance_id}")

    def drain(self, tiers: Sequence[str], timeout_seconds: float) -> bool:
        deadline = time.monotonic() + max(0, timeout_seconds)
        encoded_tiers = tuple(self._scope(tier) for tier in tiers)
        while True:
            all_drained = True
            for tier in encoded_tiers:
                query = urlencode({self._scope_key: tier})
                result = self._get(f"/deployment/drained?{query}")
                all_drained = all_drained and result.get("drained") is True
            if all_drained:
                return True
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                return False
            time.sleep(min(self.poll_interval_seconds, remaining))

    @property
    def _scope_key(self):
        return 'instance_id' if self.inventory is not None else 'tier'

    def _scope(self, value):
        if self.inventory is None:
            return _tier(value)
        if value not in {i.instance_id for i in self.inventory.instances if i.enabled}:
            raise ValueError('instance_id is not in deployment inventory')
        return value

    def _post(self, path: str, payload: dict[str, Any]) -> dict[str, Any]:
        data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        request = Request(
            self.base_url + path,
            data=data,
            headers=self._headers(),
            method="POST",
        )
        return self._send(request)

    def _get(self, path: str) -> dict[str, Any]:
        return self._send(
            Request(self.base_url + path, headers=self._headers(), method="GET")
        )

    def _headers(self) -> dict[str, str]:
        return {
            "Authorization": f"Bearer {self.token}",
            "Content-Type": "application/json",
        }

    def _send(self, request: Request) -> dict[str, Any]:
        try:
            with self.transport(request, timeout=self.timeout_seconds) as response:
                status = getattr(response, "status", 200)
                if status != 200:
                    raise RuntimeError(f"router control API returned HTTP {status}")
                payload = json.loads(response.read().decode("utf-8"))
        except Exception as exc:
            if isinstance(exc, RuntimeError):
                raise
            raise RuntimeError("router control API request failed") from None
        if not isinstance(payload, dict) or payload.get("ok") is not True:
            raise RuntimeError("router control API rejected the request")
        return payload


def _tier(value: str) -> str:
    if value not in {"debug", "test", "production", "main"}:
        raise ValueError("router tier is not allowlisted")
    return value


def _notification_id(value: str) -> str:
    if (
        not isinstance(value, str)
        or re.fullmatch(r"[A-Za-z0-9._:-]{1,180}", value) is None
    ):
        raise ValueError("notification id is not a valid stable identifier")
    return value


def validate_loopback_url(value: str) -> None:
    """Require a literal loopback IP authority, without userinfo or redirects."""
    try:
        parsed = urlsplit(value)
        host = parsed.hostname
        port = parsed.port
        is_loopback = host is not None and ipaddress.ip_address(host).is_loopback
    except ValueError:
        is_loopback = False
        port = None
        parsed = None
    if (
        parsed is None
        or parsed.scheme != "http"
        or not is_loopback
        or port is None
        or parsed.username is not None
        or parsed.password is not None
        or parsed.path not in ("", "/")
        or parsed.query
        or parsed.fragment
    ):
        raise ValueError("deployment control API must use a literal loopback HTTP URL")


def _merge_results(target: dict[str, bool], payload: dict[str, Any]) -> None:
    values = payload.get("results")
    if not isinstance(values, dict):
        raise TypeError("router announcement response has an invalid shape")
    target.update({str(group): value is True for group, value in values.items()})
