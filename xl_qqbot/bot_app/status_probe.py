"""Read-only login preflight probe for the game's maintenance state."""

from __future__ import annotations

import asyncio
import json
import os
import socket
from pathlib import Path
from urllib.request import HTTPRedirectHandler, Request, build_opener

import tomllib

from .status_monitor import StatusSample

LOGIN_URL = "https://elpis-accverify.haoplay.com.cn/v1/account/login"
ALLOWED_GATEWAY_SUFFIX = ".haoplay.com.cn"
GATEWAY_PORT = 8801
REQUEST_TIMEOUT_SECONDS = 8


class _RejectRedirects(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def _load_credentials(path: str | Path) -> dict[str, str]:
    credentials_path = Path(path)
    try:
        content = credentials_path.read_text(encoding="utf-8")
    except OSError as error:
        raise ValueError("server status credentials file is unavailable") from error

    try:
        if content.lstrip().startswith("{"):
            raw = json.loads(content)
        else:
            raw = tomllib.loads(content)
    except (json.JSONDecodeError, tomllib.TOMLDecodeError) as error:
        raise ValueError("server status credentials file is invalid JSON or TOML") from error
    if not isinstance(raw, dict):
        raise ValueError("server status credentials file must contain an object")

    credentials = {
        key: raw.get(key)
        for key in ("open_id", "token", "channel")
    }
    if any(not isinstance(value, str) or not value.strip() for value in credentials.values()):
        raise ValueError("server status credentials require open_id, token, and channel")
    if os.name == "posix" and credentials_path.stat().st_mode & 0o077:
        raise ValueError("server status credentials file must not be group/world accessible")
    return {key: value.strip() for key, value in credentials.items()}


def _gateway_endpoint(item: object) -> tuple[str, int] | None:
    if isinstance(item, str):
        host, separator, raw_port = item.rpartition(":")
        if not separator or not raw_port.isdecimal():
            return None
        port = int(raw_port)
    elif isinstance(item, dict):
        host = item.get("host", item.get("ip", item.get("address", "")))
        port = item.get("port", GATEWAY_PORT)
    else:
        return None

    if (
        not isinstance(host, str)
        or isinstance(port, bool)
        or not isinstance(port, int)
        or port != GATEWAY_PORT
    ):
        return None
    host = host.strip().rstrip(".").lower()
    if not host.endswith(ALLOWED_GATEWAY_SUFFIX) or host == ALLOWED_GATEWAY_SUFFIX[1:]:
        return None
    return host, port


def _sample(credentials: dict[str, str], timeout: float) -> StatusSample:
    payload = {
        "openId": credentials["open_id"],
        "token": credentials["token"],
        "platform": 1,
        "channel": credentials["channel"],
        "buildVersion": "",
        "authRemote": True,
    }
    request = Request(
        LOGIN_URL,
        data=json.dumps(payload, separators=(",", ":")).encode("utf-8"),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    with build_opener(_RejectRedirects).open(request, timeout=timeout) as response:
        decoded = json.loads(response.read())
    if not isinstance(decoded, dict):
        return StatusSample(False, None, False, None, False, "PARSE_ERROR")

    ret_code = decoded.get("retCode")
    if isinstance(ret_code, bool) or not isinstance(ret_code, int):
        return StatusSample(False, None, False, None, False, "PARSE_ERROR")
    if ret_code != 0:
        return StatusSample(False, ret_code, False, None, False, "AUTH_ERROR")

    data = decoded.get("data")
    if not isinstance(data, dict):
        return StatusSample(True, ret_code, False, None, False, "PARSE_ERROR")
    maintain_present = "maintainState" in data
    maintain_state = data.get("maintainState")
    addresses = data.get("addresses")
    if not isinstance(addresses, list):
        return StatusSample(
            True,
            ret_code,
            maintain_present,
            maintain_state,
            False,
            "PARSE_ERROR",
        )

    endpoints = [_gateway_endpoint(item) for item in addresses]
    endpoints = [endpoint for endpoint in endpoints if endpoint is not None]
    if not endpoints:
        return StatusSample(
            True,
            ret_code,
            maintain_present,
            maintain_state,
            False,
            "PARSE_ERROR",
        )

    network_error = False
    for endpoint in endpoints:
        try:
            with socket.create_connection(endpoint, timeout=timeout):
                return StatusSample(
                    True,
                    ret_code,
                    maintain_present,
                    maintain_state,
                    True,
                )
        except OSError:
            network_error = True
    return StatusSample(
        True,
        ret_code,
        maintain_present,
        maintain_state,
        False,
        "NETWORK_ERROR" if network_error else None,
    )


def build_login_preflight_probe(
    credentials_path: str | Path,
    *,
    timeout: float = REQUEST_TIMEOUT_SECONDS,
):
    """Load protected credentials once and return an async, session-free probe."""
    if timeout <= 0:
        raise ValueError("server status probe timeout must be positive")
    credentials = _load_credentials(credentials_path)

    async def probe() -> StatusSample:
        try:
            return await asyncio.to_thread(_sample, credentials, timeout)
        except TimeoutError:
            return StatusSample(False, None, False, None, False, "TIMEOUT")
        except OSError:
            return StatusSample(False, None, False, None, False, "NETWORK_ERROR")
        except (ValueError, TypeError, json.JSONDecodeError):
            return StatusSample(False, None, False, None, False, "PARSE_ERROR")

    return probe
