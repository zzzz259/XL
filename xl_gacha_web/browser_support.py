"""Windows browser discovery and bounded, credential-free local diagnostics."""

from __future__ import annotations

import json
import os
import re
import shutil
import socket
import tempfile
import threading
import urllib.error
import urllib.request
from datetime import datetime, timezone
from pathlib import Path


def registry_browser_paths():
    if os.name != "nt":
        return []
    import winreg

    result = []
    for hive in (winreg.HKEY_CURRENT_USER, winreg.HKEY_LOCAL_MACHINE):
        for view in (winreg.KEY_WOW64_64KEY, winreg.KEY_WOW64_32KEY):
            for exe in ("msedge.exe", "chrome.exe"):
                try:
                    with winreg.OpenKey(
                        hive,
                        rf"SOFTWARE\Microsoft\Windows\CurrentVersion\App Paths\{exe}",
                        0,
                        winreg.KEY_READ | view,
                    ) as key:
                        raw = winreg.QueryValueEx(key, None)[0]
                        if isinstance(raw, str):
                            result.append(raw.strip().strip('"'))
                except OSError:
                    pass
    return result


def discover_browsers(custom=()):
    options = list(custom)
    if os.environ.get("STARFALL_BROWSER_PATH"):
        options.append(os.environ["STARFALL_BROWSER_PATH"])
    options.extend(registry_browser_paths())
    for exe in ("msedge.exe", "chrome.exe", "chromium.exe", "brave.exe"):
        found = shutil.which(exe)
        if found:
            options.append(found)
    for base in (
        os.environ.get("PROGRAMFILES(X86)"),
        os.environ.get("PROGRAMFILES"),
        os.environ.get("LOCALAPPDATA"),
    ):
        if base:
            options.extend(
                [
                    Path(base) / "Microsoft/Edge/Application/msedge.exe",
                    Path(base) / "Google/Chrome/Application/chrome.exe",
                ]
            )
    result = []
    seen = set()
    for raw in options:
        try:
            p = Path(os.path.expandvars(str(raw).strip().strip('"'))).resolve()
            key = os.path.normcase(str(p))
            if p.suffix.lower() == ".exe" and p.is_file() and key not in seen:
                result.append(p)
                seen.add(key)
        except (OSError, ValueError):
            continue
    return result


class BrowserSettings:
    def __init__(self, directory):
        self.file = Path(directory) / "browser-settings.json"

    def paths(self):
        try:
            value = json.loads(self.file.read_text(encoding="utf-8"))
            raw = value.get("path", "")
            return [Path(raw).resolve()] if isinstance(raw, str) and raw else []
        except (OSError, ValueError, TypeError, AttributeError):
            return []

    def set_path(self, raw):
        if not isinstance(raw, str) or len(raw) > 32768:
            raise ValueError("INVALID_BROWSER_PATH")
        raw = raw.strip().strip('"')
        if raw:
            path = Path(os.path.expandvars(raw)).resolve()
            if path.suffix.lower() != ".exe" or not path.is_file():
                raise ValueError("INVALID_BROWSER_PATH")
            raw = str(path)
        self.file.parent.mkdir(parents=True, exist_ok=True)
        temp = self.file.with_suffix(".tmp")
        temp.write_text(json.dumps({"path": raw}), encoding="utf-8")
        temp.replace(self.file)


class DiagnosticLog:
    """Never accepts payloads, credential values, URLs, or exception messages."""

    def __init__(self, directory, limit=1024 * 1024):
        self.path = Path(directory) / "diagnostics" / "browser.jsonl"
        self.limit = limit
        self.lock = threading.Lock()

    def emit(self, event, **fields):
        row = {"time": datetime.now(timezone.utc).isoformat(), "event": str(event)[:64]}
        for key in (
            "browser",
            "code",
            "stage",
            "attempt",
            "launcher_running",
            "debug_healthy",
            "page_count",
            "elapsed_ms",
            "exit_code",
            "pid",
        ):
            if key not in fields:
                continue
            value = fields[key]
            if key == "browser":
                value = Path(str(value)).name[:100]
            elif key in ("code", "stage"):
                if not isinstance(value, str) or not re.fullmatch(
                    r"[A-Za-z_0-9-]{1,80}", value
                ):
                    continue
            elif not isinstance(value, (bool, int, float, type(None))):
                continue
            row[key] = value
        try:
            with self.lock:
                self.path.parent.mkdir(parents=True, exist_ok=True)
                if self.path.exists() and self.path.stat().st_size >= self.limit:
                    self.path.replace(self.path.with_suffix(".jsonl.1"))
                with self.path.open("a", encoding="utf-8") as f:
                    f.write(json.dumps(row, ensure_ascii=False) + "\n")
        except OSError:
            pass


def https_probe(url):
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    try:
        with opener.open(
            urllib.request.Request(url, method="HEAD"), timeout=8
        ) as response:
            code = response.status
        return (
            {"status": "ok", "code": "REACHABLE"}
            if code < 400
            else {"status": "warning", "code": "HTTP_BLOCKED"}
        )
    except urllib.error.HTTPError as e:
        # 405 proves the TLS/server connection works; the auth API needs POST.
        return (
            {"status": "ok", "code": "REACHABLE"}
            if e.code in (400, 401, 404, 405)
            else {"status": "warning", "code": "HTTP_BLOCKED"}
        )
    except Exception:  # noqa: BLE001 - UI/background boundary hides unexpected private error details.
        return {"status": "warning", "code": "NETWORK_UNREACHABLE"}


class EnvironmentChecks:
    def __init__(self, directory):
        self.directory = Path(directory)
        self.settings = BrowserSettings(directory)
        self.log = DiagnosticLog(directory)
        self.lock = threading.Lock()
        self.worker = None
        self.value = {"phase": "idle", "checks": []}

    def snapshot(self):
        paths = self.settings.paths()
        with self.lock:
            return {
                **self.value,
                "checks": [dict(c) for c in self.value["checks"]],
                "custom_path": str(paths[0]) if paths else "",
                "log_path": str(self.log.path),
            }

    def start(self):
        with self.lock:
            if self.worker and self.worker.is_alive():
                return
            self.value = {"phase": "running", "checks": []}
            self.worker = threading.Thread(target=self.run, daemon=True)
            self.worker.start()

    def run(self):
        checks = []
        paths = discover_browsers(self.settings.paths())
        checks.append(
            {
                "name": "browser",
                "status": "ok" if paths else "error",
                "code": "BROWSER_FOUND" if paths else "BROWSER_NOT_FOUND",
                "paths": [str(p) for p in paths],
            }
        )
        try:
            self.directory.mkdir(parents=True, exist_ok=True)
            with tempfile.TemporaryFile(
                dir=self.directory, prefix="preflight-"
            ) as probe:
                probe.write(b"ok")
                probe.flush()
            checks.append(
                {"name": "directory", "status": "ok", "code": "DIRECTORY_WRITABLE"}
            )
        except OSError:
            checks.append(
                {
                    "name": "directory",
                    "status": "error",
                    "code": "DIRECTORY_NOT_WRITABLE",
                }
            )
        try:
            with socket.socket() as listener:
                listener.settimeout(2)
                listener.bind(("127.0.0.1", 0))
                listener.listen(1)
                with socket.create_connection(listener.getsockname(), timeout=2):
                    connection, _ = listener.accept()
                    connection.close()
            checks.append(
                {"name": "loopback", "status": "ok", "code": "LOOPBACK_REACHABLE"}
            )
        except OSError:
            checks.append(
                {"name": "loopback", "status": "error", "code": "LOOPBACK_BLOCKED"}
            )
        for name, url in [
            ("official_login", "https://i.haoplay.com.cn/"),
            (
                "official_auth",
                "https://elpis-accverify.haoplay.com.cn/v1/account/login",
            ),
        ]:
            checks.append(dict(name=name, **https_probe(url)))
        for c in checks:
            self.log.emit("environment_check", stage=c["name"], code=c["code"])
        with self.lock:
            self.value = {"phase": "done", "checks": checks}
        return self.snapshot()
