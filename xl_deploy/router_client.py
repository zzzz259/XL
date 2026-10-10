"""Authenticated synchronous client for the loopback router deployment API."""

from __future__ import annotations

import ipaddress
import json
import re
import time
from collections.abc import Callable, Sequence
from typing import Any
from urllib.parse import urlencode, urlsplit
from urllib.request import HTTPRedirectHandler, Request, build_opener

START_NOTICE = "检测到更新，正在更新bot，期间将暂停服务"
COMPLETION_NOTICE = "更新完毕"


class _NoRedirectHandler(HTTPRedirectHandler):
    def redirect_request(self, request, fp, code, message, headers, new_url):
        return None


def _open_without_redirects(request: Request, *, timeout: float):
    return build_opener(_NoRedirectHandler).open(request, timeout=timeout)


class RouterControlClient:
    def __init__(
        self,
        base_url: str,
        token: str,
        *,
        transport: Callable[..., Any] | None = None,
        timeout_seconds: float = 5,
        poll_interval_seconds: float = 0.25,
    ):
        validate_loopback_url(base_url)
        if not token.strip() or timeout_seconds <= 0 or poll_interval_seconds <= 0:
            raise ValueError("router token and positive timeouts are required")
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
            safe_tier = _tier(tier)
            payload = self._post(
                "/deployment/announce",
                {
                    "tier": safe_tier,
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
            safe_tier = _tier(tier)
            payload = self._post(
                "/deployment/announce",
                {
                    "tier": safe_tier,
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
                "/deployment/maintenance", {"tier": _tier(tier), "enabled": True}
            )

    def resume(self, tiers: Sequence[str]) -> None:
        for tier in tiers:
            self._post(
                "/deployment/maintenance", {"tier": _tier(tier), "enabled": False}
            )

    def drain(self, tiers: Sequence[str], timeout_seconds: float) -> bool:
        deadline = time.monotonic() + max(0, timeout_seconds)
        encoded_tiers = tuple(_tier(tier) for tier in tiers)
        while True:
            all_drained = True
            for tier in encoded_tiers:
                query = urlencode({"tier": tier})
                result = self._get(f"/deployment/drained?{query}")
                all_drained = all_drained and result.get("drained") is True
            if all_drained:
                return True
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                return False
            time.sleep(min(self.poll_interval_seconds, remaining))

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
