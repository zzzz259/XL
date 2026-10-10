"""服务器版配置读取。"""

from __future__ import annotations

import ipaddress
import re
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

from .rerun_schedule import DEFAULT_ANCHOR

try:
    import tomllib
except ModuleNotFoundError:  # pragma: no cover - Python 3.10 fallback
    import tomli as tomllib


DEFAULT_CATEGORIES = ("Arts", "Data")
DEFAULT_BURST_ANCHOR = "2026-10-09T10:00:00+08:00"


@dataclass(frozen=True)
class ServerConfig:
    config_path: Path
    data_dir: Path
    cdn_base: str
    timezone_name: str
    normal_interval_seconds: int
    burst_interval_seconds: int
    burst_duration_seconds: int
    burst_anchor: datetime
    selected_categories: tuple[str, ...]
    java_bin: str
    unluac_jar: Path
    unluac_opmap: Path
    character_card_font: Path | None
    node_bin: str
    assetbundle_key: bytes
    environment: str = "main"
    cdn_poll_enabled: bool = True
    api_enabled: bool = False
    api_host: str = "127.0.0.1"
    api_port: int = 8790
    api_token_env: str = "XL_UPDATE_API_TOKEN"
    render_cards: bool = True
    rerun_schedule_enabled: bool = True
    rerun_schedule_anchor: dict = field(default_factory=lambda: dict(DEFAULT_ANCHOR))
    rerun_schedule_refresh_seconds: int = 3600
    rerun_schedule_forecast_limit: int = 20
    rerun_schedule_overrides: tuple[dict, ...] = ()


def _read_int(section: dict, key: str, default: int) -> int:
    value = int(section.get(key, default))
    if value <= 0:
        raise ValueError(f"{key} must be positive")
    return value


def _read_bool(section: dict, key: str, default: bool) -> bool:
    value = section.get(key, default)
    if not isinstance(value, bool):
        raise ValueError(f"{key} must be a boolean")
    return value


def load_config(path: str | Path) -> ServerConfig:
    config_path = Path(path).expanduser().resolve()
    with config_path.open("rb") as handle:
        raw = tomllib.load(handle)

    server = dict(raw.get("server", {}))
    paths = dict(raw.get("paths", {}))
    cdn = dict(raw.get("cdn", {}))
    security = dict(raw.get("security", {}))
    polling = dict(raw.get("polling", {}))
    api = dict(raw.get("api", {}))
    base_dir = config_path.parent

    environment = str(server.get("environment", "main")).strip()
    if environment not in {"test", "main"}:
        raise ValueError("server.environment must be 'test' or 'main'")
    configured_polling = _read_bool(polling, "enabled", True)
    cdn_poll_enabled = environment == "main" and configured_polling

    api_enabled = _read_bool(api, "enabled", False)
    api_host = str(api.get("host", "127.0.0.1")).strip()
    api_port = api.get("port", 8791 if environment == "test" else 8790)
    if isinstance(api_port, bool) or not isinstance(api_port, int):
        raise ValueError("api.port must be an integer")
    api_token_env = str(api.get("token_env", "XL_UPDATE_API_TOKEN")).strip()
    if api_enabled:
        try:
            is_loopback = ipaddress.ip_address(api_host).is_loopback
        except ValueError as error:
            raise ValueError("api.host must be a loopback IP address") from error
        if not is_loopback:
            raise ValueError("api.host must be a loopback IP address")
        if not 1 <= api_port <= 65535:
            raise ValueError("api.port must be between 1 and 65535")
        if not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", api_token_env):
            raise ValueError("api.token_env must be a valid environment variable name")
    categories = tuple(str(item).strip().title() for item in cdn.get("categories", DEFAULT_CATEGORIES))
    if categories != DEFAULT_CATEGORIES:
        raise ValueError("server版当前只支持 Arts 和 Data 分类")
    anchor_raw = str(server.get("burst_anchor", DEFAULT_BURST_ANCHOR)).strip()
    try:
        anchor = datetime.fromisoformat(anchor_raw)
    except ValueError as error:
        raise ValueError("burst_anchor must be a timezone-aware ISO datetime") from error
    if anchor.tzinfo is None or anchor.utcoffset() is None:
        raise ValueError("burst_anchor must be a timezone-aware ISO datetime")

    assetbundle_key = str(security.get("assetbundle_key", "yunguihaowan1234")).encode("utf-8")
    if len(assetbundle_key) != 16:
        raise ValueError("assetbundle_key must be exactly 16 UTF-8 bytes")

    cards = dict(raw.get("cards", {}))
    render_cards = bool(cards.get("enabled", True))

    rerun = dict(raw.get("rerun_schedule", {}))
    rerun_anchor = dict(DEFAULT_ANCHOR)
    configured_anchor = rerun.get("anchor", {})
    if not isinstance(configured_anchor, dict):
        raise ValueError("[rerun_schedule.anchor] 必须是表")
    rerun_anchor.update(configured_anchor)
    for key in ("start_at", "end_at"):
        try:
            parsed = datetime.fromisoformat(str(rerun_anchor[key]))
        except (KeyError, ValueError) as error:
            raise ValueError(f"[rerun_schedule.anchor] {key} 必须是带时区的 ISO datetime") from error
        if parsed.tzinfo is None or parsed.utcoffset() is None:
            raise ValueError(f"[rerun_schedule.anchor] {key} 必须是带时区的 ISO datetime")
    if datetime.fromisoformat(str(rerun_anchor["end_at"])) <= datetime.fromisoformat(str(rerun_anchor["start_at"])):
        raise ValueError("[rerun_schedule.anchor] end_at 必须晚于 start_at")
    raw_overrides = rerun.get("overrides", [])
    if not isinstance(raw_overrides, list):
        raise ValueError("[[rerun_schedule.overrides]] 必须为数组表")
    parsed_overrides: list[dict] = []
    seen_override_ids: set[int] = set()
    for index, item in enumerate(raw_overrides):
        if not isinstance(item, dict):
            raise ValueError(f"rerun_schedule.overrides[{index}] 必须是表")
        try:
            gacha_id = int(item["gacha_id"])
        except (KeyError, TypeError, ValueError) as error:
            raise ValueError(f"rerun_schedule.overrides[{index}].gacha_id 必须为正整数") from error
        source = str(item.get("source", "")).strip()
        if gacha_id <= 0 or not source or gacha_id in seen_override_ids:
            raise ValueError(f"rerun_schedule.overrides[{index}] 必须有唯一正数 gacha_id 和非空 source")
        seen_override_ids.add(gacha_id)
        override = {"gacha_id": gacha_id, "source": source}
        pool_kind = item.get("pool_kind")
        if pool_kind is not None:
            pool_kind = str(pool_kind).strip()
            if pool_kind not in ("normal", "special", "unknown"):
                raise ValueError(f"rerun_schedule.overrides[{index}].pool_kind 非法")
            override["pool_kind"] = pool_kind
        if item.get("character_id") is not None:
            try:
                override["character_id"] = int(item["character_id"])
            except (TypeError, ValueError) as error:
                raise ValueError(f"rerun_schedule.overrides[{index}].character_id 必须为整数") from error
        if "exclude_from_prediction" in item:
            if not isinstance(item["exclude_from_prediction"], bool):
                raise ValueError(f"rerun_schedule.overrides[{index}].exclude_from_prediction 必须为布尔值")
            override["exclude_from_prediction"] = item["exclude_from_prediction"]
        parsed_overrides.append(override)

    font_value = str(paths.get("character_card_font", "")).strip()
    character_card_font = (base_dir / font_value).resolve() if font_value else None

    return ServerConfig(
        config_path=config_path,
        data_dir=(base_dir / str(paths.get("data_dir", "data"))).resolve(),
        cdn_base=str(cdn.get("base", "https://elpis.17995cdn.com/Android/Bundles")).rstrip("/"),
        timezone_name=str(server.get("timezone", "Asia/Shanghai")),
        normal_interval_seconds=_read_int(server, "normal_interval_seconds", 3600),
        burst_interval_seconds=_read_int(server, "burst_interval_seconds", 60),
        burst_duration_seconds=_read_int(server, "burst_duration_seconds", 1200),
        burst_anchor=anchor,
        selected_categories=categories,
        java_bin=str(paths.get("java_bin", "java")),
        unluac_jar=(base_dir / str(paths.get("unluac_jar", "tools/lua/unluac.jar"))).resolve(),
        unluac_opmap=(base_dir / str(paths.get("unluac_opmap", "tools/lua/opmap"))).resolve(),
        character_card_font=character_card_font,
        node_bin=str(paths.get("node_bin", "node")),
        assetbundle_key=assetbundle_key,
        environment=environment,
        cdn_poll_enabled=cdn_poll_enabled,
        api_enabled=api_enabled,
        api_host=api_host,
        api_port=api_port,
        api_token_env=api_token_env,
        render_cards=render_cards,
        rerun_schedule_enabled=bool(rerun.get("enabled", True)),
        rerun_schedule_anchor=rerun_anchor,
        rerun_schedule_refresh_seconds=_read_int(rerun, "refresh_seconds", 3600),
        rerun_schedule_forecast_limit=_read_int(rerun, "forecast_limit", 20),
        rerun_schedule_overrides=tuple(parsed_overrides),
    )
