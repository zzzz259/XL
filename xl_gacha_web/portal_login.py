"""User-driven official login in an app-owned browser, credentials kept off API responses."""

from __future__ import annotations

import ctypes
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import threading
import time
import urllib.request
import uuid
from pathlib import Path
from urllib.parse import urlsplit

from browser_cdp import CDP, CdpError
from browser_support import BrowserSettings, DiagnosticLog, discover_browsers
from gacha_core.gacha import GachaError, parse_credentials

ORIGIN = "https://i.haoplay.com.cn"
KEYS = ("jwt", "HP_2070021", "HP_2070023", "lastjwt")
_SPAWN_LOCK = threading.Lock()
READ_SESSION = """(() => {
 if(location.origin!=='https://i.haoplay.com.cn') return {origin:location.origin,values:{}};
 const values={};
 for(const key of ['jwt','HP_2070021','HP_2070023','lastjwt']) values[key]=localStorage.getItem(key);
 return {origin:location.origin,provider:localStorage.getItem('provider')||'',values};
})()"""


class LoginError(Exception):
    def __init__(self, code):
        self.code = code
        super().__init__(code)


def select_credential(source):
    if not isinstance(source, dict) or source.get("origin") != ORIGIN:
        raise LoginError("PORTAL_ORIGIN_MISMATCH")
    values = source.get("values", {})
    if not isinstance(values, dict):
        return None
    provider = source.get("provider", "")
    candidates = []
    for key in KEYS:
        raw = values.get(key)
        if not raw:
            continue
        try:
            c = parse_credentials(json.dumps(raw) if isinstance(raw, dict) else raw)
        except (GachaError, TypeError, ValueError):
            continue
        if provider and c.channel != provider:
            continue
        if key == "jwt":
            return c
        candidates.append(c)
    identities = {(c.open_id, c.channel) for c in candidates}
    if len(identities) > 1:
        raise LoginError("PORTAL_AMBIGUOUS_SESSION")
    return candidates[0] if candidates else None


def portal_target(pages, port):
    for page in pages if isinstance(pages, list) else []:
        if not isinstance(page, dict) or page.get("type") != "page":
            continue
        try:
            url = urlsplit(page.get("url", ""))
            ws = urlsplit(page.get("webSocketDebuggerUrl", ""))
            if (
                url.scheme != "https"
                or url.hostname != "i.haoplay.com.cn"
                or url.port not in (None, 443)
                or url.username
                or url.password
            ):
                continue
            if (
                ws.scheme != "ws"
                or ws.hostname != "127.0.0.1"
                or ws.port != port
                or ws.username
                or ws.password
                or ws.query
                or ws.fragment
                or not ws.path.startswith("/devtools/page/")
            ):
                continue
            return page["webSocketDebuggerUrl"]
        except (TypeError, ValueError):
            continue
    return None


def browser_executable():
    options = discover_browsers()
    for option in options:
        if option.is_file():
            return option
    raise LoginError("PORTAL_BROWSER_NOT_FOUND")


def remove_profile(path, root):
    path, root = Path(path).resolve(), Path(root).resolve()
    if path.parent != root or not re.fullmatch("[a-f0-9]{32}", path.name):
        raise LoginError("PORTAL_PROFILE_INVALID")
    # Only generated, direct children of the explicit browser-profile root.
    for _ in range(20):
        try:
            shutil.rmtree(path)
            return True
        except FileNotFoundError:
            return True
        except OSError:
            time.sleep(0.1)
    return False


class ManagedBrowser:
    def __init__(
        self,
        profile,
        *,
        candidates=None,
        logger=None,
        startup_timeout=20,
        retry_interval=0.2,
        prefer_first=False,
        **kwargs,
    ):
        root = Path(profile)
        root.mkdir(parents=True, exist_ok=True)
        self.log = logger
        self.port = None
        self.process = None
        self.endpoint = None
        self.executable = None
        self.started = time.monotonic()
        self.last_alive = self.started
        self.seen_ready = False
        self.health = None
        options = list(candidates if candidates is not None else discover_browsers())
        if not options:
            raise LoginError("PORTAL_BROWSER_NOT_FOUND")
        marker = root / "starfall-browser.json"
        try:
            previous = json.loads(marker.read_text(encoding="utf-8")).get("path")
            if not prefer_first:
                options.sort(key=lambda p: str(p) != previous)
        except (OSError, ValueError, AttributeError):
            pass
        legacy = (root / "Default").exists() or (root / "DevToolsActivePort").exists()
        for attempt, exe in enumerate(options, 1):
            self.executable = Path(exe)
            # Browsers receive independent profiles, so switching to Chrome
            # cannot rewrite a remembered Edge profile (or the reverse).
            self.profile = (
                root
                if legacy and attempt == 1
                else root
                / (
                    "browser-"
                    + hashlib.sha256(str(exe).lower().encode()).hexdigest()[:16]
                )
            )
            self.profile.mkdir(parents=True, exist_ok=True)
            self.port = None
            self.endpoint = None
            self.process = None
            self.seen_ready = False
            self.started = time.monotonic()
            self.last_alive = self.started
            self._log("browser_attempt", attempt=attempt)
            if not self._endpoint_live():
                (self.profile / "DevToolsActivePort").unlink(missing_ok=True)
                self.port = None
                self.endpoint = None
                try:
                    self._launch()
                except OSError:
                    self._log("browser_start_failed", code="SPAWN_FAILED")
                    continue
            deadline = time.monotonic() + startup_timeout
            while time.monotonic() < deadline:
                if self._probe():
                    marker.write_text(json.dumps({"path": str(exe)}), encoding="utf-8")
                    self._log(
                        "browser_ready",
                        elapsed_ms=int((time.monotonic() - self.started) * 1000),
                    )
                    return
                # Launchers may hand off and exit before the real window starts.
                if not self.process_running and time.monotonic() - self.started > 3:
                    break
                time.sleep(retry_interval)
            self._log(
                "browser_start_failed",
                code="DEBUG_UNAVAILABLE",
                launcher_running=self.process_running,
            )
            self.close()
        raise LoginError("PORTAL_BROWSER_START_FAILED")

    def _launch(self):
        args = [
            str(self.executable),
            f"--user-data-dir={self.profile}",
            "--remote-debugging-address=127.0.0.1",
            "--remote-debugging-port=0",
            "--no-first-run",
            "--no-default-browser-check",
            "--disable-sync",
            "--no-proxy-server",
            ORIGIN + "/?appid=2070021&l=cn",
        ]
        # PyInstaller's DLL directory is inherited by children on Windows.
        # External browsers must start using their own libraries.
        with _SPAWN_LOCK:
            kernel = None
            previous = None
            if os.name == "nt" and getattr(sys, "frozen", False):
                kernel = ctypes.WinDLL("kernel32", use_last_error=True)
                kernel.GetDllDirectoryW.argtypes = [ctypes.c_uint32, ctypes.c_wchar_p]
                kernel.SetDllDirectoryW.argtypes = [ctypes.c_wchar_p]
                buffer = ctypes.create_unicode_buffer(32768)
                kernel.GetDllDirectoryW(len(buffer), buffer)
                previous = buffer.value or None
                kernel.SetDllDirectoryW(None)
            try:
                env = os.environ.copy()
                package = getattr(sys, "_MEIPASS", None)
                if package:
                    base = Path(package).resolve()
                    env["PATH"] = os.pathsep.join(
                        p
                        for p in env.get("PATH", "").split(os.pathsep)
                        if p and not Path(p).resolve().is_relative_to(base)
                    )
                self.process = subprocess.Popen(
                    args,
                    cwd=str(Path(args[0]).parent),
                    env=env,
                    stdin=subprocess.DEVNULL,
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                )
            finally:
                if kernel:
                    kernel.SetDllDirectoryW(previous)

    def _log(self, event, **fields):
        if getattr(self, "log", None):
            self.log.emit(event, browser=getattr(self, "executable", ""), **fields)

    @property
    def process_running(self):
        # This is the launcher handle, not proof that every Chromium child died.
        return bool(getattr(self, "process", None) and self.process.poll() is None)

    def _probe(self):
        debug = self._endpoint_live()
        pages = self.pages() if debug else []
        count = (
            sum(isinstance(p, dict) and p.get("type") == "page" for p in pages)
            if isinstance(pages, list)
            else 0
        )
        health = (self.process_running, debug, count)
        if health != getattr(self, "health", None):
            self._log(
                "browser_health",
                launcher_running=health[0],
                debug_healthy=debug,
                page_count=count,
            )
            self.health = health
        if debug and count:
            self.seen_ready = True
            self.last_alive = time.monotonic()
            return True
        return False

    @property
    def alive(self):
        now = time.monotonic()
        if self._probe():
            return True
        if not getattr(self, "seen_ready", False):
            return now - self.started < 20
        # A live process with a temporarily unavailable CDP endpoint gets a
        # longer recovery window and its own error, never a "window closed".
        return now - self.last_alive < (30 if self.process_running else 8)

    @property
    def failure_code(self):
        if not self.seen_ready:
            return "PORTAL_BROWSER_START_FAILED"
        return (
            "PORTAL_DEBUG_UNAVAILABLE"
            if self.process_running
            else "PORTAL_BROWSER_CLOSED"
        )

    def _port(self):
        if self.port:
            return self.port
        try:
            lines = (self.profile / "DevToolsActivePort").read_text().splitlines()
            n = int(lines[0])
            endpoint = lines[1]
            if 1 <= n <= 65535 and re.fullmatch(
                r"/devtools/browser/[A-Za-z0-9-]+", endpoint
            ):
                self.port = n
                self.endpoint = endpoint
        except (OSError, ValueError, IndexError):
            pass
        return self.port

    def _endpoint_live(self):
        port = self._port()
        if not port:
            return False
        try:
            opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
            with opener.open(
                f"http://127.0.0.1:{port}/json/version", timeout=1
            ) as response:
                raw = response.read(65537)
            if len(raw) > 65536:
                return False
            ws = urlsplit(json.loads(raw).get("webSocketDebuggerUrl", ""))
            return (
                ws.scheme == "ws"
                and ws.hostname == "127.0.0.1"
                and ws.port == port
                and ws.path == self.endpoint
                and not ws.username
                and not ws.password
            )
        except Exception:  # noqa: BLE001 - UI/background boundary hides unexpected private error details.
            self.port = None
            self.endpoint = None
            return False

    def pages(self):
        port = self._port()
        if not port:
            return []
        try:
            opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
            with opener.open(
                f"http://127.0.0.1:{port}/json/list", timeout=3
            ) as response:
                raw = response.read(1024 * 1024 + 1)
            return json.loads(raw) if len(raw) <= 1024 * 1024 else []
        except Exception:  # noqa: BLE001 - UI/background boundary hides unexpected private error details.
            return []

    def read(self):
        target = portal_target(self.pages(), self.port)
        if not target:
            return None
        try:
            with CDP(target, self.port) as cdp:
                reply = cdp.command(
                    "Runtime.evaluate",
                    {"expression": READ_SESSION, "returnByValue": True},
                )
                if reply.get("exceptionDetails"):
                    return None
                value = reply.get("result", {}).get("value")
                # CDP target URL can already be official while the old/about:blank
                # execution context is still active during navigation or OAuth return.
                return (
                    value
                    if isinstance(value, dict) and value.get("origin") == ORIGIN
                    else None
                )
        except CdpError:
            return None

    def close(self):
        if self._endpoint_live():
            try:
                with CDP(
                    f"ws://127.0.0.1:{self.port}{self.endpoint}", self.port
                ) as cdp:
                    cdp.command("Browser.close")
            except CdpError:
                pass
        try:
            if self.process:
                self.process.wait(timeout=4)
        except subprocess.TimeoutExpired:
            # This handle belongs only to the browser process we spawned.
            self.process.terminate()
            try:
                self.process.wait(timeout=3)
            except subprocess.TimeoutExpired:
                pass
        deadline = time.monotonic() + 3
        while time.monotonic() < deadline and self._endpoint_live():
            time.sleep(0.1)


class LoginManager:
    def __init__(
        self, directory, query, *, browser_factory=ManagedBrowser, timeout=600
    ):
        self.directory = Path(directory).resolve()
        self.settings = BrowserSettings(self.directory)
        self.log = DiagnosticLog(self.directory)
        self.query = query
        self.factory = browser_factory
        self.timeout = timeout
        self.lock = threading.Lock()
        self.stop = threading.Event()
        self.worker = None
        self.value = {"phase": "idle", "message": ""}
        self.browser = None

    def _set(self, phase, **fields):
        with self.lock:
            self.value = dict(phase=phase, **fields)

    def status(self):
        with self.lock:
            return dict(self.value)

    def profiles(self):
        root = self.directory / "login-profiles"
        result = []
        if not root.exists():
            return result
        for p in root.iterdir():
            if p.is_symlink() or not re.fullmatch("[a-f0-9]{32}", p.name):
                continue
            try:
                v = json.loads(
                    (p / "starfall-account.json").read_text(encoding="utf-8")
                )
                if isinstance(v, dict):
                    result.append(
                        {
                            "id": p.name,
                            "label": str(v.get("label", "已保存的登录"))[:80],
                            "account_id": str(v.get("account_id", ""))[:80],
                        }
                    )
            except (OSError, ValueError):
                continue
        return result

    def _existing(self, key):
        if (
            not isinstance(key, str)
            or not re.fullmatch("[a-f0-9]{32}", key)
            or not any(p["id"] == key for p in self.profiles())
        ):
            raise LoginError("PORTAL_PROFILE_INVALID")
        root = (self.directory / "login-profiles").resolve()
        path = (root / key).resolve()
        if path.parent != root:
            raise LoginError("PORTAL_PROFILE_INVALID")
        return path

    def start(self, remember=False, profile=None):
        if type(remember) is not bool:
            raise LoginError("PORTAL_PROFILE_INVALID")
        with self.lock:
            if self.worker and self.worker.is_alive():
                raise LoginError("PORTAL_BUSY")
            path = self._existing(profile) if profile and remember else None
            if profile and not remember:
                raise LoginError("PORTAL_PROFILE_INVALID")
            root = self.directory / (
                "login-profiles" if remember else "login-temporary"
            )
            path = path or root / uuid.uuid4().hex
            self.stop = threading.Event()
            self.value = {
                "phase": "opening",
                "message": "正在打开专用官网登录窗口…",
                "remember": remember,
            }
            self.worker = threading.Thread(
                target=self._run, args=(path, root, remember), daemon=True
            )
            self.worker.start()
        return self.status()

    def _run(self, path, root, remember):
        browser = None
        success = None
        error = None
        try:
            custom = self.settings.paths()
            browser = self.factory(
                path,
                candidates=discover_browsers(custom),
                logger=self.log,
                prefer_first=bool(custom or os.environ.get("STARFALL_BROWSER_PATH")),
            )
            self.browser = browser
            self._set(
                "waiting", message="请在专用浏览器窗口正常登录；登录成功后会自动查询。"
            )
            deadline = time.monotonic() + self.timeout
            while time.monotonic() < deadline and not self.stop.is_set():
                if not browser.alive:
                    raise LoginError(
                        getattr(browser, "failure_code", "PORTAL_BROWSER_CLOSED")
                    )
                source = browser.read()
                if source:
                    try:
                        credential = select_credential(source)
                    except LoginError as e:
                        if e.code == "PORTAL_AMBIGUOUS_SESSION":
                            self._set(
                                "waiting",
                                message="检测到多个旧登录状态，请在官网退出后重新登录。",
                            )
                            credential = None
                        else:
                            raise
                    if credential:
                        self._set(
                            "querying",
                            message="已获取登录状态，正在验证并查询抽卡记录…",
                        )
                        token = credential.token
                        del credential, source
                        try:
                            success = self.query({"credential": token})
                        finally:
                            token = ""
                        break
                if self.stop.wait(1):
                    break
            if not success and not self.stop.is_set():
                raise LoginError("PORTAL_TIMEOUT")
        except Exception as e:  # noqa: BLE001 - UI/background boundary hides unexpected private error details.
            code = getattr(e, "code", "PORTAL_LOGIN_FAILED")
            allowed = {
                "PORTAL_BROWSER_NOT_FOUND",
                "PORTAL_BROWSER_START_FAILED",
                "PORTAL_BROWSER_CLOSED",
                "PORTAL_DEBUG_UNAVAILABLE",
                "PORTAL_TIMEOUT",
                "PORTAL_ORIGIN_MISMATCH",
                "GATEWAY_NETWORK_ERROR",
                "AUTH_NETWORK_ERROR",
                "ACTOR_NOT_FOUND",
                "CREDENTIAL_EXPIRED",
                "QUERY_FAILED",
                "RECORD_CONFLICT",
            }
            error = (
                code
                if code in allowed
                else "PORTAL_AUTH_REJECTED"
                if str(code).startswith("AUTH_REJECTED_")
                else "PORTAL_LOGIN_FAILED"
            )
            self.log.emit("login_failed", code=error)
        finally:
            if browser:
                try:
                    browser.close()
                except Exception:  # noqa: BLE001, S110 - UI/background boundary hides unexpected private error details.
                    pass
            self.browser = None
            cleanup = True
            try:
                if not remember:
                    cleanup = remove_profile(path, root)
                elif success:
                    a = success.get("account", {})
                    (path / "starfall-account.json").write_text(
                        json.dumps(
                            {
                                "account_id": a.get("id", ""),
                                "label": a.get("label", "已保存的登录"),
                            },
                            ensure_ascii=False,
                        ),
                        encoding="utf-8",
                    )
                elif not (path / "starfall-account.json").exists():
                    cleanup = remove_profile(path, root)
            except OSError:
                cleanup = False
            if success:
                self._set(
                    "done",
                    report=success,
                    message="查询完成，已归档到本机。",
                    cleanup_pending=not cleanup,
                )
            elif self.stop.is_set():
                self._set(
                    "cancelled", message="已取消登录。", cleanup_pending=not cleanup
                )
            else:
                self._set(
                    "error",
                    error=error or "PORTAL_LOGIN_FAILED",
                    cleanup_pending=not cleanup,
                )

    def cancel(self):
        if self.status()["phase"] == "querying":
            raise LoginError("PORTAL_QUERY_IN_PROGRESS")
        self.stop.set()
        return {"phase": "cancelling", "message": "正在关闭专用登录窗口…"}

    def forget(self, key):
        if self.worker and self.worker.is_alive():
            raise LoginError("PORTAL_BUSY")
        path = self._existing(key)
        if not remove_profile(path, self.directory / "login-profiles"):
            raise LoginError("PORTAL_PROFILE_IN_USE")

    def close(self):
        self.stop.set()
        if self.worker and self.worker.is_alive():
            self.worker.join(timeout=10)
