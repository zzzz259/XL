"""Local Starfall browser application; persists history, never credentials."""

from __future__ import annotations

import argparse
import json
import socket
import sys
import threading
import time
from collections import defaultdict, deque
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlsplit

from archive import MAX_FILE, Archive, ArchiveError
from browser_support import EnvironmentChecks
from catalog_updates import CatalogStore, CatalogUpdateError
from gacha_core.gacha import GachaError, fetch_history, parse_credentials
from gacha_core.gacha_report import Catalog, make_report
from portal_login import LoginError, LoginManager

ROOT = Path(__file__).resolve().parent
APP_HOME = (
    Path(sys.executable).resolve().parent if getattr(sys, "frozen", False) else ROOT
)
MAX_BODY = 70_000


class RequestError(Exception):
    def __init__(self, code: str, status: int = 400):
        self.code, self.status = code, status
        super().__init__(code)


def allowed_origin(origin: str | None, origins: set[str]) -> bool:
    return isinstance(origin, str) and origin in origins


class QueryService:
    def __init__(self, archive=None, catalog=None):
        self.catalog = catalog or Catalog(ROOT / "data")
        self.archive = archive
        self.slots = threading.BoundedSemaphore(2)

    def query(self, payload: dict) -> dict:
        if not isinstance(payload, dict) or not isinstance(
            payload.get("credential"), str
        ):
            raise RequestError("INVALID_INPUT")
        text = payload["credential"].strip()
        if not text or len(text) > 65536:
            raise RequestError("INVALID_INPUT")
        if not self.slots.acquire(blocking=False):
            raise RequestError("BUSY", 429)
        try:
            credentials = parse_credentials(text)
            del text
            catalog = self.catalog
            records, queries = fetch_history(credentials, catalog.selectors)
            uid, channel = credentials.open_id, credentials.channel
            del credentials
            if not any(query["ret_code"] == 0 for query in queries):
                raise RequestError("ALL_HISTORY_QUERIES_REJECTED", 502)
            report = make_report(records, catalog, queries)
            report["partial"] = any(query["ret_code"] != 0 for query in queries)
            return (
                self.archive.save_query(report, uid, channel, catalog=catalog)
                if self.archive
                else report
            )
        except RequestError:
            raise
        except GachaError as error:
            local = {
                "INVALID_CREDENTIAL_INPUT",
                "CREDENTIAL_EXPIRED",
                "WRONG_APP",
                "INPUT_TOO_LARGE",
                "DEVICE_MISMATCH",
                "PROVIDER_MISMATCH",
                "AMBIGUOUS_CREDENTIAL_INPUT",
                "APP_ID_MISMATCH",
            }
            raise RequestError(
                error.code, 400 if error.code in local else 502
            ) from None
        except ArchiveError as error:
            raise RequestError(str(error)) from None
        except Exception:  # noqa: BLE001 - UI/background boundary hides unexpected private error details.
            raise RequestError("QUERY_FAILED", 502) from None
        finally:
            self.slots.release()


class WebServer(ThreadingHTTPServer):
    daemon_threads = True
    allow_reuse_address = False

    def server_bind(self):
        # Windows SO_REUSEADDR can silently share a live listener with an old app.
        if hasattr(socket, "SO_EXCLUSIVEADDRUSE"):
            self.socket.setsockopt(socket.SOL_SOCKET, socket.SO_EXCLUSIVEADDRUSE, 1)
        super().server_bind()

    def __init__(
        self, address, public_origin=None, preview_report=None, archive_dir=None
    ):
        super().__init__(address, Handler)
        port = self.server_port
        self.origins = {f"http://127.0.0.1:{port}", f"http://localhost:{port}"}
        if public_origin:
            parsed = urlsplit(public_origin)
            if (
                parsed.scheme != "https"
                or not parsed.netloc
                or parsed.path
                or parsed.query
                or parsed.fragment
                or parsed.username
            ):
                self.server_close()
                raise ValueError("public-origin must be an HTTPS origin without path")
            self.origins.add(public_origin)
        self.hosts = {urlsplit(origin).netloc for origin in self.origins}
        directory = Path(archive_dir or APP_HOME / "user-data")
        default_source = json.loads(
            (ROOT / "data" / "update-source.json").read_text(encoding="utf-8")
        )["url"]
        self.catalog_store = CatalogStore(
            directory / "catalog-cache",
            json.loads((ROOT / "data" / "catalog.json").read_text(encoding="utf-8")),
            source=default_source,
        )
        self.archive = Archive(directory, self.catalog_store.catalog())
        self.service = QueryService(self.archive, self.catalog_store.catalog())

        def apply_catalog(catalog):
            self.archive.use_catalog(catalog)
            self.service.catalog = catalog

        self.catalog_store.on_change = apply_catalog
        self.login = LoginManager(self.archive.directory, self.service.query)
        self.environment = EnvironmentChecks(self.archive.directory)
        self.preview_report = (
            json.loads(Path(preview_report).read_text(encoding="utf-8"))
            if preview_report
            else None
        )
        self.rate_lock = threading.Lock()
        self.requests = defaultdict(deque)

    def server_close(self):
        if hasattr(self, "login"):
            self.login.close()
        super().server_close()

    def handle_error(self, request, client_address):
        # The stdlib default prints tracebacks which may retain request objects.
        pass

    def permit(self, ip):
        now = time.monotonic()
        with self.rate_lock:
            # Keep this memory bounded, including on a reverse-proxied deployment.
            for key in list(self.requests):
                q = self.requests[key]
                while q and q[0] < now - 60:
                    q.popleft()
                if not q:
                    del self.requests[key]
            q = self.requests[ip]
            if len(q) >= 6 or len(self.requests) > 4096:
                return False
            q.append(now)
            return True


class Handler(BaseHTTPRequestHandler):
    server_version = "StarfallGacha"
    sys_version = ""

    def setup(self):
        super().setup()
        self.connection.settimeout(15)

    def log_message(self, *_):
        pass

    def send_data(self, status, data, content_type="application/json; charset=utf-8"):
        # Windows may reset a connection closed with an unread request body,
        # hiding small POST rejection responses. Drain only a bounded body.
        if (
            status >= 400
            and self.command == "POST"
            and not getattr(self, "body_consumed", False)
        ):
            try:
                length = int(self.headers.get("Content-Length", "0"))
                if 0 < length <= 131072:
                    self.rfile.read(length)
            except (OSError, ValueError):
                pass
            self.body_consumed = True
        if isinstance(data, dict):
            data = json.dumps(data, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Referrer-Policy", "no-referrer")
        self.send_header("X-Frame-Options", "DENY")
        self.send_header(
            "Content-Security-Policy",
            "default-src 'self'; script-src 'self'; style-src 'self'; img-src 'self' data: blob:; connect-src 'self'; frame-ancestors 'none'; base-uri 'none'; form-action 'self'",
        )
        self.end_headers()
        self.wfile.write(data)

    def do_GET(self):
        if self.headers.get("Host") not in self.server.hosts:
            return self.send_data(403, {"error": "HOST_NOT_ALLOWED"})
        path = urlsplit(self.path).path
        if path == "/api/library":
            return self.send_data(
                200,
                {
                    "accounts": self.server.archive.list_accounts(),
                    "last_account": self.server.archive.last_account(),
                    "directory": str(self.server.archive.directory),
                },
            )
        if path == "/api/login":
            return self.send_data(
                200,
                dict(
                    **self.server.login.status(), profiles=self.server.login.profiles()
                ),
            )
        if path == "/api/environment":
            return self.send_data(200, self.server.environment.snapshot())
        if path == "/api/catalog":
            return self.send_data(200, self.server.catalog_store.status())
        if path == "/preview.json" and self.server.preview_report is not None:
            return self.send_data(200, self.server.preview_report)
        if path == "/healthz":
            return self.send_data(200, {"status": "ok"})
        assets = {
            "/": ("index.html", "text/html; charset=utf-8"),
            "/app.js": ("app.js", "text/javascript; charset=utf-8"),
            "/demo.json": ("demo.json", "application/json; charset=utf-8"),
            "/style.css": ("style.css", "text/css; charset=utf-8"),
            "/theme.css": ("theme.css", "text/css; charset=utf-8"),
            "/local.js": ("local.js", "text/javascript; charset=utf-8"),
            "/login.js": ("login.js", "text/javascript; charset=utf-8"),
            "/environment.js": ("environment.js", "text/javascript; charset=utf-8"),
            "/catalog-ui.js": ("catalog-ui.js", "text/javascript; charset=utf-8"),
            "/image-export.js": ("image-export.js", "text/javascript; charset=utf-8"),
            "/local.css": ("local.css", "text/css; charset=utf-8"),
            "/favicon.svg": ("favicon.svg", "image/svg+xml"),
        }
        if path not in assets:
            return self.send_data(404, {"error": "NOT_FOUND"})
        name, mime = assets[path]
        return self.send_data(200, (ROOT / "static" / name).read_bytes(), mime)

    def do_POST(self):
        if self.headers.get("Host") not in self.server.hosts or not allowed_origin(
            self.headers.get("Origin"), self.server.origins
        ):
            return self.send_data(403, {"error": "ORIGIN_NOT_ALLOWED"})
        library_actions = {
            "/api/library/open",
            "/api/library/rename",
            "/api/library/delete",
            "/api/library/import",
            "/api/library/backup",
            "/api/login/start",
            "/api/login/cancel",
            "/api/login/forget",
            "/api/environment/check",
            "/api/environment/browser",
            "/api/catalog/check",
            "/api/catalog/source",
        }
        if self.path != "/api/query" and self.path not in library_actions:
            return self.send_data(404, {"error": "NOT_FOUND"})
        if (
            self.headers.get("Content-Type", "").split(";")[0].strip().lower()
            != "application/json"
        ):
            return self.send_data(415, {"error": "JSON_REQUIRED"})
        try:
            length = int(self.headers.get("Content-Length", "0"))
        except ValueError:
            length = 0
        limit = MAX_FILE * 2 if self.path == "/api/library/import" else MAX_BODY
        if length <= 0 or length > limit:
            return self.send_data(413, {"error": "INPUT_TOO_LARGE"})
        if self.path in ("/api/query", "/api/login/start") and not self.server.permit(
            self.client_address[0]
        ):
            return self.send_data(429, {"error": "RATE_LIMITED"})
        try:
            raw = self.rfile.read(length)
            self.body_consumed = True
            if len(raw) != length:
                raise RequestError("INVALID_INPUT")
            payload = json.loads(raw)
            del raw
            if not isinstance(payload, dict):
                raise RequestError("INVALID_INPUT")
            if self.path == "/api/query":
                report = self.server.service.query(payload)
            elif self.path == "/api/catalog/check":
                report = self.server.catalog_store.start(
                    force=payload.get("force", True) is not False
                )
            elif self.path == "/api/catalog/source":
                self.server.catalog_store.set_source(payload.get("url"))
                report = self.server.catalog_store.start(force=True)
            elif self.path == "/api/environment/check":
                self.server.environment.start()
                report = self.server.environment.snapshot()
            elif self.path == "/api/environment/browser":
                if self.server.login.status()["phase"] in (
                    "opening",
                    "waiting",
                    "querying",
                ):
                    raise RequestError("PORTAL_BUSY")
                try:
                    self.server.environment.settings.set_path(payload.get("path"))
                except (ValueError, OSError):
                    raise RequestError("INVALID_BROWSER_PATH") from None
                self.server.environment.start()
                report = self.server.environment.snapshot()
            elif self.path == "/api/login/start":
                report = self.server.login.start(
                    payload.get("remember", False), payload.get("profile")
                )
            elif self.path == "/api/login/cancel":
                report = self.server.login.cancel()
            elif self.path == "/api/login/forget":
                self.server.login.forget(payload.get("profile"))
                report = {"deleted": True}
            elif self.path == "/api/library/backup":
                report = self.server.archive.backup()
            elif self.path == "/api/library/import":
                report = {
                    "reports": self.server.archive.import_data(
                        payload.get("content"),
                        payload.get("format"),
                        payload.get("target"),
                        payload.get("label", ""),
                    )
                }
            else:
                key = payload.get("id")
                if not isinstance(key, str) or len(key) > 80:
                    raise RequestError("INVALID_INPUT")
                action = self.path.rsplit("/", 1)[1]
                if action == "open":
                    report = self.server.archive.open(key)
                elif action == "rename":
                    report = self.server.archive.rename(key, payload.get("label"))
                else:
                    self.server.archive.delete(key)
                    report = {"deleted": True}
            del payload
            return self.send_data(200, report)
        except RequestError as error:
            return self.send_data(error.status, {"error": error.code})
        except ArchiveError as error:
            return self.send_data(400, {"error": str(error)})
        except CatalogUpdateError as error:
            return self.send_data(400, {"error": str(error)})
        except LoginError as error:
            return self.send_data(
                409
                if error.code in ("PORTAL_BUSY", "PORTAL_QUERY_IN_PROGRESS")
                else 400,
                {"error": error.code},
            )
        except (ValueError, UnicodeError, RecursionError):
            return self.send_data(400, {"error": "INVALID_INPUT"})
        except (TimeoutError, OSError):
            return
        except Exception:  # noqa: BLE001 - UI/background boundary hides unexpected private error details.
            return self.send_data(500, {"error": "LOCAL_STORAGE_ERROR"})


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--host", default="127.0.0.1", choices=["127.0.0.1", "localhost"]
    )
    parser.add_argument("--port", type=int, default=8788)
    parser.add_argument(
        "--public-origin", help="Exact HTTPS origin served by your reverse proxy"
    )
    parser.add_argument(
        "--preview-report",
        type=Path,
        help="Explicit local report for design preview; not bundled",
    )
    parser.add_argument(
        "--data-dir",
        type=Path,
        help="Local archive directory; defaults to user-data beside the app",
    )
    parser.add_argument("--open", action="store_true", help="Open the default browser")
    args = parser.parse_args()
    server = WebServer(
        (args.host, args.port), args.public_origin, args.preview_report, args.data_dir
    )
    server.catalog_store.start()
    print(f"Starfall gacha web: http://127.0.0.1:{server.server_port}", flush=True)
    if args.open:
        import webbrowser

        webbrowser.open(f"http://127.0.0.1:{server.server_port}")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()


if __name__ == "__main__":
    main()
