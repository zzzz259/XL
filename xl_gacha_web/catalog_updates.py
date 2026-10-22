"""Data-only catalog updates. No credentials, scripts, or game requests here."""

from __future__ import annotations

import hashlib
import json
import re
import threading
import time
import urllib.request
from datetime import UTC, datetime
from pathlib import Path
from urllib.parse import urljoin, urlsplit

from gacha_core.gacha_report import Catalog

MAX_CATALOG = 4 * 1024 * 1024
MAX_MANIFEST = 16 * 1024
INTERVAL = 6 * 3600


class CatalogUpdateError(ValueError):
    pass


def fail(code="CATALOG_INVALID"):
    raise CatalogUpdateError(code)


def version(value):
    if not isinstance(value, str) or not re.fullmatch(r"[1-9][0-9]{0,19}", value):
        fail()
    return int(value)


def integer(value, minimum=1, maximum=2**53 - 1):
    if type(value) is not int or not minimum <= value <= maximum:
        fail()
    return value


def label(value):
    if (
        not isinstance(value, str)
        or not value.strip()
        or len(value) > 120
        or any(ord(c) < 32 or 127 <= ord(c) <= 159 for c in value)
    ):
        fail()
    return value


def validate_source(source):
    if source == "":
        return source
    if not isinstance(source, str) or len(source) > 2048:
        fail("CATALOG_SOURCE_INVALID")
    try:
        u = urlsplit(source)
        if (
            u.scheme != "https"
            or not u.hostname
            or u.username
            or u.password
            or u.query
            or u.fragment
            or not u.path.endswith("/current.json")
        ):
            fail("CATALOG_SOURCE_INVALID")
        if u.port is not None and not 1 <= u.port <= 65535:
            fail("CATALOG_SOURCE_INVALID")
        if (
            "%" in u.path
            or "//" in u.path
            or any(p in (".", "..") for p in u.path.split("/"))
        ):
            fail("CATALOG_SOURCE_INVALID")
        if any(c.isspace() or ord(c) < 32 for c in source) or "\\" in source:
            fail("CATALOG_SOURCE_INVALID")
    except ValueError:
        fail("CATALOG_SOURCE_INVALID")
    return source


def validate_manifest(m):
    if (
        not isinstance(m, dict)
        or m.get("format") != "starfall-gacha-catalog-manifest"
        or type(m.get("schema_version")) is not int
        or m["schema_version"] != 1
    ):
        fail("CATALOG_SCHEMA_UNSUPPORTED")
    version(m.get("version"))
    if m.get("file") != f"catalog-{m['version']}.json":
        fail()
    if not isinstance(m.get("sha256"), str) or not re.fullmatch(
        r"[a-f0-9]{64}", m["sha256"]
    ):
        fail()
    integer(m.get("size"), 1, MAX_CATALOG)
    return m


def validate_catalog(c):
    if (
        not isinstance(c, dict)
        or type(c.get("schema_version")) is not int
        or c["schema_version"] != 1
    ):
        fail("CATALOG_SCHEMA_UNSUPPORTED")
    version(c.get("version"))
    for key in ("cards", "pools", "pool_meta"):
        if not isinstance(c.get(key), dict) or not 1 <= len(c[key]) <= 20000:
            fail()
    for key, item in c["cards"].items():
        if not isinstance(key, str) or not re.fullmatch(r"[1-9][0-9]*", key):
            fail()
        integer(int(key))
        if not isinstance(item, list) or len(item) != 2:
            fail()
        label(item[0])
        if item[1] is not None:
            integer(item[1], 1, 5)
    if set(c["pools"]) != set(c["pool_meta"]):
        fail()
    for key, name in c["pools"].items():
        if not isinstance(key, str) or not re.fullmatch(r"[1-9][0-9]*", key):
            fail()
        integer(int(key))
        label(name)
        m = c["pool_meta"][key]
        if not isinstance(m, dict) or m.get("category") not in (
            "normal",
            "limited",
            "collection",
            "beginner",
            "unknown",
        ):
            fail()
        if not isinstance(m.get("pity_group"), str) or not re.fullmatch(
            r"[A-Za-z0-9:_-]{1,80}", m["pity_group"]
        ):
            fail()
        if "hard_pity" not in m:
            fail()
        if m["hard_pity"] is not None:
            integer(m["hard_pity"], 1, 1000)
        ids = m.get("featured_ids")
        names = m.get("featured_names")
        if (
            not isinstance(ids, list)
            or not isinstance(names, list)
            or len(ids) > 128
            or len(ids) != len(names)
        ):
            fail()
        for item, name in zip(ids, names):
            integer(item)
            if (
                str(item) not in c["cards"]
                or c["cards"][str(item)][1] != 5
                or name != c["cards"][str(item)][0]
            ):
                fail()
        if len(set(ids)) != len(ids):
            fail()
    selectors = c.get("selectors")
    if not isinstance(selectors, list) or not 1 <= len(selectors) <= 8:
        fail()
    for item in selectors:
        integer(item)
    if len(set(selectors)) != len(selectors):
        fail()
    # Strip unknown fields so even a valid document remains data-only.
    return {
        k: c[k]
        for k in (
            "schema_version",
            "version",
            "cards",
            "pools",
            "selectors",
            "pool_meta",
        )
    }


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *args, **kwargs):
        return None


def fetch_bytes(url, limit):
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}), NoRedirect())
    request = urllib.request.Request(
        url,
        headers={
            "Accept": "application/json",
            "Accept-Encoding": "identity",
            "User-Agent": "StarfallGacha-Catalog/1",
        },
    )
    with opener.open(request, timeout=12) as response:
        raw = response.read(limit + 1)
    if len(raw) > limit:
        fail("CATALOG_TOO_LARGE")
    return raw


def atomic(path, raw):
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_suffix(path.suffix + ".part")
    with temp.open("wb") as f:
        f.write(raw)
        f.flush()
        import os

        os.fsync(f.fileno())
    temp.replace(path)


class CatalogStore:
    def __init__(self, directory, bundled, *, source="", fetch=fetch_bytes):
        self.root = Path(directory)
        self.root.mkdir(parents=True, exist_ok=True)
        self.lock = threading.RLock()
        self.operation = threading.Lock()
        self.worker = None
        self.fetch = fetch
        self.on_change = None
        self.phase = "idle"
        self.error = ""
        self.checked_at = 0
        self.retry_at = 0
        self.saved_at = ""
        self.remote = False
        bundled = {**bundled, "schema_version": 1}
        data = validate_catalog(bundled)
        raw = json.dumps(
            data, ensure_ascii=False, sort_keys=True, separators=(",", ":")
        ).encode()
        self.current = Catalog.from_data(data, hashlib.sha256(raw).hexdigest())
        self.pointer = None
        self.source = validate_source(source)
        try:
            self.source = validate_source(
                json.loads((self.root / "source.json").read_text(encoding="utf-8"))[
                    "url"
                ]
            )
        except (OSError, ValueError, KeyError, TypeError):
            pass
        for pointer in ("active.json", "previous.json"):
            try:
                cached = json.loads((self.root / pointer).read_text(encoding="utf-8"))
                m = validate_manifest(cached["manifest"])
                with (self.root / (m["sha256"] + ".json")).open("rb") as stream:
                    raw = stream.read(MAX_CATALOG + 1)
                data = self._verified(raw, m)
                if version(data["version"]) < version(self.current.version):
                    continue
                self.current = Catalog.from_data(data, m["sha256"])
                self.remote = True
                self.saved_at = cached.get("saved_at", "")
                self.pointer = cached
                self.checked_at = (
                    float(cached.get("checked_at", 0))
                    if cached.get("source") == self.source
                    else 0
                )
                break
            except (OSError, ValueError, TypeError, KeyError):
                continue

    @staticmethod
    def _verified(raw, m):
        if (
            len(raw) != m["size"]
            or len(raw) > MAX_CATALOG
            or hashlib.sha256(raw).hexdigest() != m["sha256"]
        ):
            fail("CATALOG_HASH_MISMATCH")
        c = validate_catalog(json.loads(raw.decode("utf-8")))
        if c["version"] != m["version"]:
            fail("CATALOG_VERSION_MISMATCH")
        return c

    def catalog(self):
        with self.lock:
            return self.current

    def status(self):
        with self.lock:
            return {
                "phase": "unconfigured" if not self.source else self.phase,
                "version": self.current.version,
                "revision": self.current.fingerprint,
                "source": self.source,
                "updated_at": self.saved_at,
                "checked_at": self.checked_at,
                "error": self.error,
                "origin": "cache" if self.remote else "bundled",
            }

    def set_source(self, source):
        source = validate_source(source)
        with self.lock:
            atomic(self.root / "source.json", json.dumps({"url": source}).encode())
            self.source = source
            self.checked_at = 0
            self.retry_at = 0
            self.error = ""
            self.phase = "idle"
        return self.status()

    def start(self, force=False):
        with self.lock:
            if self.worker and self.worker.is_alive():
                return self.status()
            if not self.source or (
                not force
                and (
                    time.time() < self.retry_at
                    or time.time() - self.checked_at < INTERVAL
                )
            ):
                return self.status()
            self.phase = "checking"
            self.error = ""
            self.worker = threading.Thread(
                target=self.check, args=(force,), daemon=True
            )
            self.worker.start()
        return self.status()

    def check(self, force=False):
        if not self.operation.acquire(blocking=False):
            return {**self.status(), "updated": False}
        try:
            with self.lock:
                source = self.source
                if not source or (
                    not force
                    and (
                        time.time() < self.retry_at
                        or time.time() - self.checked_at < INTERVAL
                    )
                ):
                    return {**self.status(), "updated": False}
                self.phase = "checking"
                self.error = ""
            raw_manifest = self.fetch(source, MAX_MANIFEST)
            if len(raw_manifest) > MAX_MANIFEST:
                fail("CATALOG_TOO_LARGE")
            m = validate_manifest(json.loads(raw_manifest.decode("utf-8")))
            with self.lock:
                current = self.current
                if version(m["version"]) < version(current.version):
                    fail("CATALOG_DOWNGRADE_REJECTED")
                if self.remote and m["version"] == current.version:
                    if m["sha256"] != current.fingerprint:
                        fail("CATALOG_VERSION_CONFLICT")
                    if source != self.source:
                        fail("CATALOG_SOURCE_CHANGED")
                    self.checked_at = time.time()
                    self.phase = "current"
                    pointer = self.root / "active.json"
                    if self.pointer:
                        cached = {
                            **self.pointer,
                            "checked_at": self.checked_at,
                            "source": source,
                        }
                        self.pointer = cached
                        atomic(pointer, json.dumps(cached).encode())
                    return {**self.status(), "updated": False}
            raw = self.fetch(urljoin(source, m["file"]), MAX_CATALOG)
            data = self._verified(raw, m)
            new = Catalog.from_data(data, m["sha256"])
            with self.lock:
                if source != self.source:
                    fail("CATALOG_SOURCE_CHANGED")
                atomic(self.root / (m["sha256"] + ".json"), raw)
                pointer = self.root / "active.json"
                if self.pointer:
                    atomic(
                        self.root / "previous.json", json.dumps(self.pointer).encode()
                    )
                now = time.time()
                saved = datetime.now(UTC).isoformat()
                next_pointer = {
                    "manifest": m,
                    "saved_at": saved,
                    "checked_at": now,
                    "source": source,
                }
                atomic(pointer, json.dumps(next_pointer).encode())
                self.pointer = next_pointer
                self.current = new
                self.remote = True
                self.saved_at = saved
                self.checked_at = now
                self.phase = "updated"
                self.retry_at = 0
            if self.on_change:
                self.on_change(new)
            return {**self.status(), "updated": True}
        except Exception as e:  # noqa: BLE001 - UI/background boundary hides unexpected private error details.
            with self.lock:
                code = (
                    str(e)
                    if isinstance(e, CatalogUpdateError)
                    else "CATALOG_NETWORK_OR_DATA_ERROR"
                )
                self.phase = "error"
                self.error = code
                self.retry_at = time.time() + 300
            return {**self.status(), "updated": False}
        finally:
            self.operation.release()
