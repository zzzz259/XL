"""CDN 更新元数据与 Bundle 下载客户端。"""

from __future__ import annotations

import hashlib
import json
import ssl
import urllib.request
from dataclasses import dataclass
from pathlib import Path

HEADERS = {
    "User-Agent": "UnityPlayer/2021.3.45f2c1 (UnityWebRequest/1.0, libcurl/8.5.0-DEV)",
    "X-Unity-Version": "2021.3.45f2c1",
}


def validate_remote_name(value: str) -> str:
    """Require an unambiguous relative POSIX name, never a caller URL."""
    if type(value) is not str or not value or len(value) > 4096:
        raise ValueError("invalid remote name")
    if any(ord(c) < 33 or ord(c) == 127 for c in value) or any(c in value for c in "\\:?#%"):
        raise ValueError("unsafe remote name")
    if any(part in ("", ".", "..") for part in value.split("/")):
        raise ValueError("unsafe remote name")
    return value


@dataclass(frozen=True)
class UpdateInfo:
    timestamp: int
    file: str


@dataclass(frozen=True)
class CategoryInfo:
    name: str
    hash: str
    size: int
    version: int


@dataclass(frozen=True)
class VersionCatalog:
    timestamp: int
    categories: tuple[CategoryInfo, ...]


class CdnClient:
    def __init__(self, base_url: str, timeout_seconds: float = 30.0):
        self.base_url = base_url.rstrip("/")
        self.timeout_seconds = timeout_seconds
        self.ssl_context = ssl.create_default_context()

    def _get_bytes(self, url: str) -> bytes:
        request = urllib.request.Request(url, headers=HEADERS)
        with urllib.request.urlopen(request, context=self.ssl_context, timeout=self.timeout_seconds) as response:
            return response.read()

    def fetch_version_bytes(self, version_file: str, max_bytes: int = 8 * 1024 * 1024) -> bytes:
        """Fetch exact original version bytes with a hard response limit.

        Kept separate from legacy _get_bytes mocks/consumers intentionally.
        """
        validate_remote_name(version_file)
        if type(max_bytes) is not int or not 0 < max_bytes <= 8 * 1024 * 1024:
            raise ValueError("invalid version response limit")
        request = urllib.request.Request(f"{self.base_url}/{version_file}", headers=HEADERS)
        chunks, total = [], 0
        with urllib.request.urlopen(request, context=self.ssl_context, timeout=self.timeout_seconds) as response:
            while chunk := response.read(min(1024 * 1024, max_bytes - total + 1)):
                total += len(chunk)
                if total > max_bytes:
                    raise ValueError("version response too large")
                chunks.append(chunk)
        return b"".join(chunks)

    def fetch_update_info(self) -> UpdateInfo:
        url = f"{self.base_url.rsplit('/Android/Bundles', 1)[0]}/Android/UpdateInfo/updateinfo.json"
        payload = json.loads(self._get_bytes(url).decode("utf-8"))
        return UpdateInfo(timestamp=int(payload["timestamp"]), file=str(payload["file"]))

    def fetch_version_catalog(self, version_file: str) -> VersionCatalog:
        validate_remote_name(version_file)
        payload = json.loads(self._get_bytes(f"{self.base_url}/{version_file}").decode("utf-8"))
        return self.parse_version_catalog(payload)

    @staticmethod
    def parse_version_catalog(payload: dict) -> VersionCatalog:
        categories = tuple(
            CategoryInfo(
                name=str(item["name"]),
                hash=str(item["hash"]),
                size=int(item.get("size", 0)),
                version=int(item.get("ver", 0)),
            )
            for item in payload.get("data", ())
            if str(item.get("name", "")) in {"Arts", "Data"}
        )
        return VersionCatalog(timestamp=int(payload["timestamp"]), categories=categories)

    def download_file(
        self,
        remote_name: str,
        destination: str | Path,
        expected_hash: str | None = None,
    ) -> Path:
        destination = Path(destination)
        destination.parent.mkdir(parents=True, exist_ok=True)
        partial = destination.with_name(destination.name + ".part")
        request = urllib.request.Request(f"{self.base_url}/{remote_name}", headers=HEADERS)
        digest = hashlib.md5()
        with urllib.request.urlopen(request, context=self.ssl_context, timeout=self.timeout_seconds) as response, partial.open("wb") as output:
            while chunk := response.read(1024 * 1024):
                digest.update(chunk)
                output.write(chunk)
        if expected_hash and digest.hexdigest().lower() != expected_hash.lower():
            partial.unlink(missing_ok=True)
            raise ValueError(f"MD5 校验失败: {remote_name}")
        partial.replace(destination)
        return destination

    def download_bundle(self, bundle_hash: str, destination: str | Path) -> Path:
        return self.download_file(f"{bundle_hash}.bundle", destination, expected_hash=bundle_hash)

    def download_category_catalog(self, category: str, category_hash: str, destination: str | Path) -> Path:
        return self.download_file(
            f"{category.lower()}_{category_hash}.json",
            destination,
            expected_hash=category_hash,
        )
