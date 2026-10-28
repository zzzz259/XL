"""Bounded gacha-history client. Secrets enter through stdin and stay in memory."""

from __future__ import annotations

import argparse
import base64
import getpass
import json
import re
import socket
import struct
import sys
import time
import urllib.error
import urllib.request
import uuid
from dataclasses import dataclass
from pathlib import Path

AUTH_LOGIN_URL = "https://elpis-accverify.haoplay.com.cn/v1/account/login"
SUPPORTED_APP_IDS = frozenset({"2070021", "2070023"})


class GachaError(Exception):
    def __init__(self, code):
        self.code = code
        super().__init__(code)


@dataclass(frozen=True, repr=False)
class WebCredentials:
    token: str
    open_id: str
    channel: str
    device_id: str


def parse_credentials(raw: str, *, now=None, supported_app_ids=SUPPORTED_APP_IDS) -> WebCredentials:
    """Accept JWT, HP JSON, or one supported HP application wrapper."""
    if len(raw) > 65536:
        raise GachaError("INPUT_TOO_LARGE")
    try:
        value = raw.strip()
        outer = {}
        wrapper_app = None
        if value.startswith("{"):
            outer = json.loads(value)
            wrappers = [app for app in supported_app_ids if f"HP_{app}" in outer]
            if len(wrappers) > 1:
                raise GachaError("AMBIGUOUS_CREDENTIAL_INPUT")
            wrapper_app = wrappers[0] if wrappers else None
            value = outer[f"HP_{wrapper_app}"] if wrapper_app else outer
            if isinstance(value, str) and value.lstrip().startswith("{"):
                value = json.loads(value)
            if isinstance(value, dict):
                value = value["jwt"]
        if not isinstance(value, str) or len(value.split(".")) != 3:
            raise ValueError()
        body = value.split(".")[1]
        claims = json.loads(base64.urlsafe_b64decode(body + "=" * (-len(body) % 4)))
        app_id = str(claims.get("appid"))
        if app_id not in supported_app_ids:
            raise GachaError("WRONG_APP")
        if wrapper_app is not None and wrapper_app != app_id:
            raise GachaError("APP_ID_MISMATCH")
        expires = claims.get("exp")
        if (
            isinstance(expires, bool)
            or not isinstance(expires, (int, float))
            or expires <= (time.time() if now is None else now)
        ):
            raise GachaError("CREDENTIAL_EXPIRED")
        uid, provider = claims.get("uid"), claims.get("provider")
        if isinstance(uid, bool) or not isinstance(uid, (int, str)) or not str(uid):
            raise ValueError()
        if not isinstance(provider, str) or not re.fullmatch(
            r"[A-Za-z0-9_-]{1,64}", provider
        ):
            raise ValueError()
        device = str(uuid.UUID(claims["guid"]))
        for key in ("guid", "deviceid"):
            if key in outer and str(outer[key]) != device:
                raise GachaError("DEVICE_MISMATCH")
        if "provider" in outer and outer["provider"] != provider:
            raise GachaError("PROVIDER_MISMATCH")
        return WebCredentials(value, str(uid), provider, device)
    except GachaError:
        raise
    except (ValueError, TypeError, KeyError, AttributeError, UnicodeError):
        raise GachaError("INVALID_CREDENTIAL_INPUT") from None


def varint(value):
    value &= (1 << 64) - 1
    out = bytearray()
    while value > 127:
        out.append((value & 127) | 128)
        value >>= 7
    out.append(value)
    return bytes(out)


def integer(number, value):
    return varint(number << 3) + varint(value)


def blob(number, value):
    return varint((number << 3) | 2) + varint(len(value)) + value


def fields(data):
    offset = 0
    result = []

    def read_varint():
        nonlocal offset
        value = 0
        for shift in range(0, 70, 7):
            if offset >= len(data):
                break
            byte = data[offset]
            offset += 1
            value |= (byte & 127) << shift
            if not byte & 128:
                return value
        raise GachaError("INVALID_PROTOBUF")

    while offset < len(data):
        tag = read_varint()
        number, wire = tag >> 3, tag & 7
        if not number:
            raise GachaError("INVALID_PROTOBUF")
        if wire == 0:
            value = read_varint()
        elif wire in (1, 2, 5):
            length = read_varint() if wire == 2 else {1: 8, 5: 4}[wire]
            if length > len(data) - offset:
                raise GachaError("TRUNCATED_PROTOBUF")
            value = data[offset : offset + length]
            offset += length
        else:
            raise GachaError("INVALID_PROTOBUF")
        result.append((number, wire, value))
    return result


def numeric(data):
    return {n: v for n, w, v in fields(data) if w == 0}


def signed(value):
    value &= 0xFFFFFFFF
    return value - 0x100000000 if value & 0x80000000 else value


def frame(command, body, sequence):
    head = integer(1, command) + integer(3, sequence)
    message = blob(1, head) + blob(2, body)
    if len(message) > 65535:
        raise GachaError("REQUEST_TOO_LARGE")
    return struct.pack("!HHHH", 0, len(message), 0, command & 65535) + message


def exact(sock, length):
    out = bytearray()
    while len(out) < length:
        part = sock.recv(length - len(out))
        if not part:
            raise GachaError("CONNECTION_CLOSED")
        out.extend(part)
    return bytes(out)


def response(sock, expected, timeout=20):
    deadline = time.monotonic() + timeout
    for _ in range(128):
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise GachaError("RESPONSE_TIMEOUT")
        sock.settimeout(remaining)
        magic, length, _, _ = struct.unpack("!HHHH", exact(sock, 8))
        if magic != 0 or not length:
            raise GachaError("INVALID_FRAME")
        message = fields(exact(sock, length))
        head = next((numeric(v) for n, w, v in message if n == 1 and w == 2), {})
        if head.get(1) == expected:
            body = next((v for n, w, v in message if n == 2 and w == 2), b"")
            return signed(head.get(4, 0)), body
    raise GachaError("RESPONSE_LIMIT")


def allowed_gateway(host, port):
    return (
        isinstance(host, str)
        and re.fullmatch(r"[A-Za-z0-9.-]+\.haoplay\.com\.cn", host)
        and port == 8801
    )


def gateway_connection(host, port, *, timeout=12):
    if not allowed_gateway(host, port):
        raise GachaError("GATEWAY_NOT_ALLOWED")
    try:
        return socket.create_connection((host, port), timeout=timeout)
    except OSError:
        raise GachaError("GATEWAY_NETWORK_ERROR") from None


def authenticate(credentials, *, timeout=15):
    payload = {
        "openId": credentials.open_id,
        "token": credentials.token,
        "platform": 1,
        "channel": credentials.channel,
        "buildVersion": "",
        "authRemote": True,
    }
    request = urllib.request.Request(
        AUTH_LOGIN_URL,
        data=json.dumps(payload).encode(),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    # Explicitly disable environment and Windows system HTTP proxy discovery.
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    try:
        with opener.open(request, timeout=timeout) as reply:
            raw = reply.read(65537)
        if len(raw) > 65536:
            raise GachaError("AUTH_RESPONSE_TOO_LARGE")
        auth = json.loads(raw)
    except urllib.error.HTTPError as error:
        raise GachaError(f"AUTH_HTTP_{error.code}") from None
    except (OSError, urllib.error.URLError):
        raise GachaError("AUTH_NETWORK_ERROR") from None
    except (ValueError, UnicodeError):
        raise GachaError("INVALID_AUTH_RESPONSE") from None
    if not isinstance(auth, dict) or type(auth.get("retCode")) is not int:
        raise GachaError("INVALID_AUTH_RESPONSE")
    if auth["retCode"] != 0:
        raise GachaError(f"AUTH_REJECTED_{auth['retCode']}")
    data = auth.get("data")
    if not isinstance(data, dict):
        raise GachaError("INVALID_AUTH_RESPONSE")
    if type(data.get("maintainState")) in (int, float) and data["maintainState"] == 1:
        raise GachaError("SERVER_MAINTENANCE")
    for address in data.get("addresses", []):
        if not isinstance(address, str) or ":" not in address:
            continue
        host, port = address.rsplit(":", 1)
        if port == "8801" and allowed_gateway(host, 8801):
            return host, 8801
    raise GachaError("NO_ALLOWED_GATEWAY")


def enter_frame(credentials):
    body = b"".join(
        [
            blob(1, credentials.token.encode()),
            blob(3, credentials.device_id.encode()),
            blob(4, b"haoplay_cn"),
            integer(5, 1),
            integer(6, 1),
            blob(7, credentials.open_id.encode()),
        ]
    )
    return frame(1002, body, 1)


def decode_records(body):
    records = []
    for n, w, data in fields(body):
        if n != 1 or w != 2:
            continue
        nums = numeric(data)
        drops = []
        for dn, dw, drop in fields(data):
            if dn == 2 and dw == 2:
                item = numeric(drop)
                drops.append(
                    {
                        "tuple_type": item.get(1, 0),
                        "item_id": item.get(2, 0),
                        "count": item.get(3, 0),
                    }
                )
        records.append(
            {
                "pool_id": nums.get(1, 0),
                "timestamp": nums.get(3, 0),
                "index": nums.get(5, 0),
                "record_id": nums.get(6, 0),
                "drops": drops,
            }
        )
    return records


def merge_records(batches):
    records = {}
    for batch in batches:
        for record in batch:
            # Include order/content when sequence fields are absent; do not merge
            # repeated items within a multi-pull record.
            key = json.dumps(record, sort_keys=True, separators=(",", ":"))
            records[key] = record
    return sorted(
        records.values(),
        key=lambda r: (r["timestamp"], r["pool_id"], r["index"], r["record_id"]),
    )


def fetch_history(credentials, selectors, progress=None):
    selectors = list(dict.fromkeys(selectors))
    if (
        not selectors
        or len(selectors) > 8
        or any(type(n) is not int or n <= 0 for n in selectors)
    ):
        raise GachaError("INVALID_QUERY_SCOPE")
    host, port = authenticate(credentials)
    if progress:
        progress({"stage": "authentication", "accepted": True})
    sock = gateway_connection(host, port)
    batches, results = [], []
    try:
        sock.sendall(enter_frame(credentials))
        code, body = response(sock, 101002)
        if code != 0:
            raise GachaError(f"ENTER_REJECTED_{code}")
        if numeric(body).get(1, 0) != 1:
            raise GachaError("ACTOR_NOT_FOUND")
        if progress:
            progress({"stage": "game_entry", "ret_code": 0, "actor_exists": True})
        for sequence, selector in enumerate(selectors, 2):
            sock.sendall(frame(10504, integer(1, selector), sequence))
            code, body = response(sock, 110504)
            records = decode_records(body) if code == 0 else []
            batches.append(records)
            result = {
                "selector": selector,
                "ret_code": code,
                "record_count": len(records),
            }
            results.append(result)
            if progress:
                progress(dict(stage="gacha_history", **result))
            if sequence < len(selectors) + 1:
                time.sleep(1)
        return merge_records(batches), results
    finally:
        sock.close()


def main():
    from .gacha_report import Catalog, make_report, write_reports

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--lua-root", required=True, type=Path)
    parser.add_argument("--output-dir", required=True, type=Path)
    parser.add_argument(
        "--selectors",
        type=int,
        nargs="+",
        help="override client-derived selectors (max 8)",
    )
    args = parser.parse_args()
    try:
        catalog = Catalog(args.lua_root)
        print(
            '{"stage":"awaiting_credentials","input":"one-line JWT or JSON on stdin"}',
            flush=True,
        )
        raw = (
            getpass.getpass("Credential (hidden): ")
            if sys.stdin.isatty()
            else sys.stdin.readline(65538)
        )
        credentials = parse_credentials(raw)
        del raw
        records, results = fetch_history(
            credentials,
            args.selectors or catalog.selectors,
            progress=lambda event: print(json.dumps(event), flush=True),
        )
        del credentials
        if not any(result["ret_code"] == 0 for result in results):
            raise GachaError("ALL_HISTORY_QUERIES_REJECTED")
        report = make_report(records, catalog, results)
        paths = write_reports(report, args.output_dir)
        print(
            json.dumps(
                {
                    "result": "EXPORTED",
                    "summary": report["summary"],
                    "files": {k: str(v) for k, v in paths.items()},
                },
                ensure_ascii=False,
            ),
            flush=True,
        )
    except GachaError as error:
        print(json.dumps({"result": "FAILED", "code": error.code}), flush=True)
        raise SystemExit(1)
    except Exception:  # noqa: BLE001 - UI/background boundary hides unexpected private error details.
        # Never include arbitrary network, JSON, filesystem, or input exception text.
        print('{"result":"FAILED","code":"QUERY_OR_EXPORT_ERROR"}', flush=True)
        raise SystemExit(1)


if __name__ == "__main__":
    main()
