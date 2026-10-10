"""Loopback-only authenticated control API for on-demand update processing."""

from __future__ import annotations

import hmac
import ipaddress
import json
import os
import re
import socket
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Mapping
from urllib.parse import urlsplit

from .config import ServerConfig
from .update_coordinator import UpdateCoordinator

MAX_REQUEST_BYTES = 4096
_JOB_ID = re.compile(r"[A-Za-z0-9-]{1,64}\Z")


def read_api_token(name: str, environ: Mapping[str, str] | None = None) -> str:
    values = os.environ if environ is None else environ
    token = str(values.get(name, "")).strip()
    if len(token) < 32 or any(character.isspace() for character in token):
        raise RuntimeError("update API bearer token is missing or too short")
    return token


def build_control_server(
    config: ServerConfig,
    coordinator: UpdateCoordinator,
    token: str,
    *,
    port_override: int | None = None,
) -> ThreadingHTTPServer:
    if not config.api_enabled:
        raise ValueError("update control API is disabled")
    try:
        is_loopback = ipaddress.ip_address(config.api_host).is_loopback
    except ValueError as error:
        raise ValueError("update API host must be a loopback IP address") from error
    if not is_loopback:
        raise ValueError("update API host must be a loopback IP address")
    if len(token) < 32 or any(character.isspace() for character in token):
        raise ValueError("update API bearer token is missing or too short")
    port = config.api_port if port_override is None else port_override
    if not 0 <= port <= 65535 or (port == 0 and port_override is None):
        raise ValueError("update API port is invalid")

    handler = _handler_type(config, coordinator, token)
    server_type = ThreadingHTTPServer
    if ":" in config.api_host:
        server_type = type(
            "IPv6LoopbackThreadingHTTPServer",
            (ThreadingHTTPServer,),
            {"address_family": socket.AF_INET6},
        )
    return server_type((config.api_host, port), handler)


def _handler_type(config: ServerConfig, coordinator: UpdateCoordinator, token: str):
    class ControlRequestHandler(BaseHTTPRequestHandler):
        protocol_version = "HTTP/1.0"

        def do_GET(self):
            path = urlsplit(self.path).path
            if path == "/healthz":
                return self._json(
                    200,
                    {
                        "ok": True,
                        "environment": config.environment,
                        "cdn_poll_enabled": config.cdn_poll_enabled,
                    },
                )
            job_match = re.fullmatch(r"/api/v1/jobs/([^/]+)", path)
            if path != "/api/v1/status" and job_match is None:
                return self._error(404, "not found")
            if not self._authorized():
                return self._error(401, "unauthorized")
            if path == "/api/v1/status":
                return self._json(200, coordinator.status())
            if job_match:
                job_id = job_match.group(1)
                if not _JOB_ID.fullmatch(job_id):
                    return self._error(404, "job not found")
                job = coordinator.get_job(job_id)
                if job is None:
                    return self._error(404, "job not found")
                return self._json(200, job)
            return self._error(404, "not found")

        def do_POST(self):
            path = urlsplit(self.path).path
            if path != "/api/v1/updates/run-once":
                return self._error(404, "not found")
            if not self._authorized():
                return self._error(401, "unauthorized")
            if self.headers.get("Transfer-Encoding"):
                self.close_connection = True
                return self._error(400, "request body is not supported")
            try:
                content_length = int(self.headers.get("Content-Length", "0"))
            except ValueError:
                return self._error(400, "invalid content length")
            if content_length < 0:
                return self._error(400, "invalid content length")
            if content_length > MAX_REQUEST_BYTES:
                self.close_connection = True
                return self._error(413, "request body too large")
            if content_length:
                self.rfile.read(content_length)
                self.close_connection = True
                return self._error(400, "request body is not supported")
            status = coordinator.status()
            if not status.get("accepting", True):
                return self._error(503, "update service is stopping")
            if not status.get("running") and not status.get("active_job_id"):
                try:
                    job_id = coordinator.enqueue_manual()
                except RuntimeError:
                    return self._error(503, "update service is stopping")
                except Exception:
                    return self._error(503, "update job could not be queued")
            else:
                job_id = None
            if job_id is None:
                status = coordinator.status()
                if not status.get("accepting", True):
                    return self._error(503, "update service is stopping")
                return self._json(
                    409,
                    {
                        "ok": False,
                        "error": "update already running or service stopping",
                        "active_job_id": status.get("active_job_id"),
                    },
                )
            return self._json(202, {"ok": True, "job_id": job_id, "status": "queued"})

        def do_PUT(self):
            return self._error(405, "method not allowed")

        def do_DELETE(self):
            return self._error(405, "method not allowed")

        def _authorized(self) -> bool:
            header = self.headers.get("Authorization", "")
            scheme, separator, supplied = header.partition(" ")
            valid_scheme = separator == " " and scheme == "Bearer"
            supplied_bytes = supplied.encode("utf-8") if valid_scheme else b""
            return hmac.compare_digest(token.encode("utf-8"), supplied_bytes)

        def _json(self, status_code: int, payload: dict):
            encoded = json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
            self.send_response(status_code)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Content-Length", str(len(encoded)))
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(encoded)

        def _error(self, status_code: int, message: str):
            return self._json(status_code, {"ok": False, "error": message})

        def log_message(self, _format: str, *_args):
            # Authorization headers are never logged; avoid logging request bodies too.
            return

    return ControlRequestHandler
