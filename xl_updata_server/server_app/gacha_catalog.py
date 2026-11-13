"""Static, non-personal gacha data export and immutable public publication.

No Lua is executed. This module needs only the Python standard library.
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
import re
import tempfile
from datetime import UTC, datetime
from pathlib import Path

from .gacha_adapter import parse_lua_table

MAX_BYTES = 4 * 1024 * 1024
MAX_ID = 2**53 - 1
TABLES = (
    "BaseCard",
    "BaseWord_cn",
    "BaseGacha",
    "BaseGachaBottomUp",
    "BaseGachaBottomPool",
)
CATEGORIES = {"normal", "limited", "collection", "beginner", "unknown"}
LOGGER = logging.getLogger(__name__)


def _version(value):
    if not isinstance(value, str) or not re.fullmatch(r"[1-9][0-9]{0,19}", value):
        raise ValueError("version must be a decimal string of at most 20 characters")
    return value


def _integer(value, maximum=MAX_ID):
    return type(value) is int and 0 < value <= maximum


def _name(value):
    return (
        isinstance(value, str)
        and 0 < len(value) <= 120
        and bool(value.strip())
        and not any(ord(char) < 32 or 127 <= ord(char) <= 159 for char in value)
    )


def validate_catalog(catalog: dict) -> None:
    """Reject incomplete, unbounded or inconsistent data before exposing it."""
    if (
        not isinstance(catalog, dict)
        or type(catalog.get("schema_version")) is not int
        or catalog["schema_version"] != 1
    ):
        raise ValueError("unsupported catalog schema")
    _version(catalog.get("version"))
    for field in ("cards", "pools"):
        values = catalog.get(field)
        if not isinstance(values, dict) or not 0 < len(values) <= 20000:
            raise ValueError(f"invalid {field} count")
        for key, value in values.items():
            if (
                not isinstance(key, str)
                or not re.fullmatch(r"[1-9][0-9]*", key)
                or int(key) > MAX_ID
            ):
                raise ValueError(f"invalid {field} ID")
            if field == "cards":
                if (
                    not isinstance(value, list)
                    or len(value) != 2
                    or not _name(value[0])
                    or not (value[1] is None or _integer(value[1], 5))
                ):
                    raise ValueError("invalid card")
            elif not _name(value):
                raise ValueError("invalid pool name")
    selectors = catalog.get("selectors")
    if (
        not isinstance(selectors, list)
        or not 0 < len(selectors) <= 8
        or not all(_integer(value) for value in selectors)
        or len(set(selectors)) != len(selectors)
    ):
        raise ValueError("invalid selectors")
    metadata = catalog.get("pool_meta")
    if not isinstance(metadata, dict) or metadata.keys() != catalog["pools"].keys():
        raise ValueError("pool metadata must cover all pools")
    for meta in metadata.values():
        if (
            not isinstance(meta, dict)
            or not isinstance(meta.get("category"), str)
            or meta["category"] not in CATEGORIES
        ):
            raise ValueError("invalid pool category")
        group = meta.get("pity_group")
        if not isinstance(group, str) or not re.fullmatch(
            r"[A-Za-z0-9:_-]{1,80}", group
        ):
            raise ValueError("invalid pity group")
        if "hard_pity" not in meta:
            raise ValueError("missing hard pity")
        pity = meta["hard_pity"]
        if pity is not None and not _integer(pity, 1000):
            raise ValueError("invalid hard pity")
        ids, names = meta.get("featured_ids"), meta.get("featured_names")
        if (
            not isinstance(ids, list)
            or not isinstance(names, list)
            or len(ids) > 128
            or len(ids) != len(names)
            or not all(_integer(value) for value in ids)
            or len(set(ids)) != len(ids)
        ):
            raise ValueError("invalid featured cards")
        for card_id, name in zip(ids, names):
            card = catalog["cards"].get(str(card_id))
            if card is None or card[1] != 5 or card[0] != name:
                raise ValueError("invalid featured reference")


def _json_bytes(payload):
    return (
        json.dumps(
            payload,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        )
        + "\n"
    ).encode("utf-8")


def _catalog_bytes(catalog):
    validate_catalog(catalog)
    raw = _json_bytes(catalog)
    if len(raw) > MAX_BYTES:
        raise ValueError("catalog exceeds 4 MiB")
    return raw


def build_gacha_catalog(lua_dir: Path | str, version: str) -> dict:
    """Build a client catalog from all five decoded tables, never from account data."""
    _version(version)
    paths = {
        path.name.casefold(): path for path in Path(lua_dir).iterdir() if path.is_file()
    }
    tables = {}
    for name in TABLES:
        path = paths.get((name + ".lua").casefold())
        if path is None:
            raise FileNotFoundError(f"missing {name}.lua")
        tables[name] = parse_lua_table(path.read_text(encoding="utf-8-sig"), name)
        if not tables[name]:
            raise ValueError(f"empty table {name}")
    words = tables["BaseWord_cn"]

    def name(record, fallback):
        value = record.get("name")
        if isinstance(value, dict):
            reference = value.get("__translation__")
            word = words.get(reference, words.get(str(reference)))
            value = word.get("name") if isinstance(word, dict) else None
        return value.strip() if isinstance(value, str) and value.strip() else fallback

    def array(value):
        if isinstance(value, list):
            return value
        if isinstance(value, dict) and set(value) == set(range(1, len(value) + 1)):
            return [value[key] for key in range(1, len(value) + 1)]
        raise ValueError("invalid Lua array")

    cards = {
        str(key): [name(record, f"未知角色({key})"), record.get("star")]
        for key, record in tables["BaseCard"].items()
        if isinstance(record, dict)
    }
    pools, metadata, selectors = {}, {}, set()
    for key, record in tables["BaseGacha"].items():
        if not isinstance(record, dict):
            continue
        pool_id = str(key)
        pools[pool_id] = name(record, f"未知卡池({key})")
        group, kind = record.get("bottom_main_type", 0), record.get("type", 0)
        selectors.add(group or key)
        bottom = tables["BaseGachaBottomUp"].get(record.get("bottom_up"), {})
        bottom_type = bottom.get("bottom_type") if isinstance(bottom, dict) else None
        category = (
            "normal"
            if kind == 0
            else "beginner"
            if kind == 1
            else "limited"
            if group == 1 and bottom_type == 201
            else "collection"
            if group == 2 and bottom_type == 301
            else "unknown"
        )
        featured = [
            card
            for card in array(record.get("main_card_ids", []))
            if cards.get(str(card), ["", None])[1] == 5
        ]
        hard_pity = None
        for bottom_id in array(record.get("bottom_pool", [])):
            bottom_pool = tables["BaseGachaBottomPool"].get(bottom_id, {})
            if isinstance(bottom_pool, dict) and bottom_pool.get("type") == 1:
                hard_pity = bottom_pool.get("bottom_num")
                break
        metadata[pool_id] = {
            "category": category,
            "pity_group": category if category != "unknown" else f"unknown:{pool_id}",
            "featured_ids": featured,
            "featured_names": [cards[str(card)][0] for card in featured],
            "hard_pity": hard_pity,
        }
    result = {
        "schema_version": 1,
        "version": version,
        "cards": cards,
        "pools": pools,
        "selectors": sorted(selectors),
        "pool_meta": metadata,
    }
    _catalog_bytes(result)
    return result


def stage_gacha_catalog(
    lua_dir: Path | str, staging: Path | str, version: str
) -> tuple[str, ...]:
    """Export before decoded Lua cleanup, keeping character processing independent."""
    try:
        catalog = build_gacha_catalog(lua_dir, version)
        publish_catalog(catalog, Path(staging) / "gacha_config")
        LOGGER.info(
            "stage=gacha_catalog version=%s cards=%d pools=%d status=success",
            version,
            len(catalog["cards"]),
            len(catalog["pools"]),
        )
        return ()
    except Exception as error:  # noqa: BLE001 - UI/background boundary hides unexpected private error details.
        LOGGER.warning(
            "stage=gacha_catalog version=%s status=skipped fallback=last_known_good error=%s",
            version,
            error,
        )
        return (f"gacha catalog: {error}"[:500],)


def read_published_catalog(directory: Path | str) -> tuple[dict, dict]:
    """Read only a bounded, digest-verified immutable catalog under this directory."""
    directory = Path(directory)
    manifest_path = directory / "current.json"
    if manifest_path.stat().st_size > 16384:
        raise ValueError("oversized catalog manifest")
    manifest = json.loads(manifest_path.read_bytes())
    version = _version(manifest.get("version"))
    filename = f"catalog-{version}.json"
    if (
        manifest.get("format") != "starfall-gacha-catalog-manifest"
        or type(manifest.get("schema_version")) is not int
        or manifest["schema_version"] != 1
        or manifest.get("file") != filename
        or not _integer(manifest.get("size"), MAX_BYTES)
    ):
        raise ValueError("invalid catalog manifest")
    path = directory / filename
    if not path.resolve().is_relative_to(directory.resolve()):
        raise ValueError("catalog path escapes publication directory")
    if path.stat().st_size != manifest["size"]:
        raise ValueError("catalog size mismatch")
    raw = path.read_bytes()
    if hashlib.sha256(raw).hexdigest() != manifest.get("sha256"):
        raise ValueError("catalog digest mismatch")
    catalog = json.loads(raw)
    validate_catalog(catalog)
    if catalog["version"] != version:
        raise ValueError("catalog version mismatch")
    return catalog, manifest


def _write_temporary(directory, raw):
    descriptor, filename = tempfile.mkstemp(
        prefix=".catalog-", suffix=".part", dir=directory
    )
    try:
        with os.fdopen(descriptor, "wb") as handle:
            handle.write(raw)
            handle.flush()
            os.fsync(handle.fileno())
        os.chmod(filename, 0o644)  # Only the two public JSON artifacts use this writer.
    except BaseException:
        Path(filename).unlink(missing_ok=True)
        raise
    return Path(filename)


def publish_catalog(catalog: dict, directory: Path | str) -> dict:
    """Write immutable data first, then atomically switch the public manifest.

    A lock rejects concurrent publishers; a crash may leave .publish.lock, which
    an operator can remove after verifying no publisher is running.
    """
    raw = _catalog_bytes(catalog)
    directory = Path(directory)
    directory.mkdir(parents=True, exist_ok=True)
    directory.chmod(0o755)
    lock = directory / ".publish.lock"
    descriptor = os.open(lock, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
    os.close(descriptor)
    try:
        version = catalog["version"]
        previous = None
        if (directory / "current.json").exists():
            _, previous = read_published_catalog(directory)
            if int(version) < int(previous["version"]):
                raise ValueError("catalog downgrade forbidden")
        filename = f"catalog-{version}.json"
        target = directory / filename
        if target.exists():
            if target.read_bytes() != raw:
                raise ValueError("immutable catalog version content conflict")
            target.chmod(0o644)
        else:
            temporary = _write_temporary(directory, raw)
            try:
                os.rename(
                    temporary, target
                )  # Atomic under the exclusive publication lock.
            finally:
                temporary.unlink(missing_ok=True)
        if previous and previous["version"] == version:
            (directory / "current.json").chmod(0o644)
            return previous
        manifest = {
            "format": "starfall-gacha-catalog-manifest",
            "schema_version": 1,
            "version": version,
            "file": filename,
            "sha256": hashlib.sha256(raw).hexdigest(),
            "size": len(raw),
            "published_at": datetime.now(UTC).isoformat(),
        }
        temporary = _write_temporary(directory, _json_bytes(manifest))
        try:
            os.replace(temporary, directory / "current.json")
        finally:
            temporary.unlink(missing_ok=True)
        return manifest
    finally:
        lock.unlink(missing_ok=True)


def main() -> None:
    import argparse

    parser = argparse.ArgumentParser(
        description="Export public gacha JSON from five decoded Lua tables; never executes Lua."
    )
    parser.add_argument("--lua-root", required=True, type=Path)
    parser.add_argument(
        "--version",
        required=True,
        help="Game version: decimal string, at most 20 digits",
    )
    parser.add_argument(
        "--output", required=True, type=Path, help="Public gacha_config directory"
    )
    args = parser.parse_args()
    catalog = build_gacha_catalog(args.lua_root, args.version)
    manifest = publish_catalog(catalog, args.output)
    print(json.dumps(manifest, ensure_ascii=True, sort_keys=True))


if __name__ == "__main__":
    main()
