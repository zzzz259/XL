import ipaddress
import json
import re
import socket
import urllib.request
from urllib.parse import urlparse

from .core import LOGIN_URL, SDK_URL, SafeError


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *args, **kwargs):
        return None


class ExplicitProxy(urllib.request.ProxyHandler):
    """Ignore NO_PROXY for this explicitly configured HTTPS proxy."""

    def https_open(self, request):
        request.set_proxy(f"{self.address[0]}:{self.address[1]}", "http")
        return None


class HTTP:
    def __init__(self, proxy="http://127.0.0.1:7897", timeout=15):
        if proxy == "direct":
            self.proxy = None
            self.timeout = max(1, min(30, timeout))
            self.opener = urllib.request.build_opener(
                urllib.request.ProxyHandler({}), NoRedirect()
            )
            return
        try:
            p = urlparse(proxy)
            if (
                p.scheme != "http"
                or not ipaddress.ip_address(p.hostname).is_loopback
                or not p.port
            ):
                raise ValueError()
            if (
                p.username
                or p.password
                or p.path not in ("", "/")
                or p.query
                or p.fragment
            ):
                raise ValueError()
            self.proxy = (p.hostname, p.port)
        except (ValueError, TypeError):
            raise SafeError("LOOPBACK_HTTP_PROXY_REQUIRED") from None
        self.timeout = max(1, min(30, timeout))
        handler = ExplicitProxy({})
        handler.address = self.proxy
        self.opener = urllib.request.build_opener(handler, NoRedirect())

    def __call__(self, url, body, headers):
        if url not in (SDK_URL, LOGIN_URL):
            raise SafeError("ENDPOINT_NOT_ALLOWED")
        try:
            request = urllib.request.Request(
                url, data=body, headers=headers, method="POST"
            )
            with self.opener.open(request, timeout=self.timeout) as response:
                raw = response.read(1048577)
                if len(raw) > 1048576:
                    raise ValueError()
                obj = json.loads(raw)
                if not isinstance(obj, dict):
                    raise ValueError()
                return obj
        except Exception:
            raise SafeError("HTTP_FAILED") from None

    def gateway(self, addresses):
        if not isinstance(addresses, list):
            return "UNKNOWN"
        targets = []
        for a in addresses:
            if isinstance(a, dict):
                host = a.get("host", a.get("ip", a.get("address")))
                port = a.get("port", 8801)
                a = (
                    f"{host}:{port}"
                    if isinstance(host, str) and type(port) is int
                    else ""
                )
            if isinstance(a, str) and re.fullmatch(
                r"[a-zA-Z0-9.-]+\.haoplay\.com\.cn:8801", a
            ):
                if a not in targets:
                    targets.append(a)
        if not targets:
            return "UNKNOWN"
        for address in targets[:4]:
            try:
                if self.proxy is None:
                    host, port = address.rsplit(":", 1)
                    with socket.create_connection((host, int(port)), timeout=3):
                        return "REACHABLE"
                with socket.create_connection(self.proxy, timeout=3) as connection:
                    connection.sendall(
                        f"CONNECT {address} HTTP/1.1\r\nHost: {address}\r\n\r\n".encode(
                            "ascii"
                        )
                    )
                    response = b""
                    while b"\r\n\r\n" not in response and len(response) < 4096:
                        chunk = connection.recv(512)
                        if not chunk:
                            break
                        response += chunk
                    if re.match(rb"HTTP/1\.[01] 200(?: |\r)", response):
                        return "REACHABLE"
            except OSError:
                pass
        return "UNREACHABLE"
