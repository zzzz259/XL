"""服务器版配置读取。"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

try:
    import tomllib
except ModuleNotFoundError:  # pragma: no cover - Python 3.10 fallback
    import tomli as tomllib


DEFAULT_CATEGORIES = ("Arts", "Data")


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
    render_cards: bool = True


def _read_int(section: dict, key: str, default: int) -> int:
    value = int(section.get(key, default))
    if value <= 0:
        raise ValueError(f"{key} must be positive")
    return value


def load_config(path: str | Path) -> ServerConfig:
    config_path = Path(path).expanduser().resolve()
    with config_path.open("rb") as handle:
        raw = tomllib.load(handle)

    server = dict(raw.get("server", {}))
    paths = dict(raw.get("paths", {}))
    cdn = dict(raw.get("cdn", {}))
    security = dict(raw.get("security", {}))
    base_dir = config_path.parent
    categories = tuple(str(item).strip().title() for item in cdn.get("categories", DEFAULT_CATEGORIES))
    if categories != DEFAULT_CATEGORIES:
        raise ValueError("server版当前只支持 Arts 和 Data 分类")
    anchor_raw = str(server.get("burst_anchor", "last_friday")).strip()
    if anchor_raw.lower() in ("last_friday", "auto"):
        # 动态锚点：以配置时区最近一个周五 10:00 为基准，每 21 天一次密集检查，
        # 不写死日期，避免锚点随时间漂移失效
        from datetime import timedelta
        from zoneinfo import ZoneInfo

        timezone_name = str(server.get("timezone", "Asia/Shanghai"))
        now = datetime.now(ZoneInfo(timezone_name))
        days_since_friday = (now.weekday() - 4) % 7
        friday = (now - timedelta(days=days_since_friday)).date()
        anchor = datetime(friday.year, friday.month, friday.day, 10, 0, tzinfo=ZoneInfo(timezone_name))
    else:
        anchor = datetime.fromisoformat(anchor_raw)
        if anchor.tzinfo is None:
            raise ValueError("burst_anchor must include a timezone offset")

    assetbundle_key = str(security.get("assetbundle_key", "yunguihaowan1234")).encode("utf-8")
    if len(assetbundle_key) != 16:
        raise ValueError("assetbundle_key must be exactly 16 UTF-8 bytes")

    cards = dict(raw.get("cards", {}))
    render_cards = bool(cards.get("enabled", True))

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
        render_cards=render_cards,
    )
