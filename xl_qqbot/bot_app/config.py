import os
import re
import tomllib
from dataclasses import dataclass, field
from typing import Dict, List
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

_HHMM_RE = re.compile(r"^(\d{1,2}):(\d{2})$")


def _validate_hhmm(value: str) -> None:
    match = _HHMM_RE.match(value)
    if not match or int(match.group(1)) > 23 or int(match.group(2)) > 59:
        raise ValueError(f"[bilibili] burst_times 格式应为 HH:MM: {value!r}")


@dataclass(frozen=True)
class BotConfig:
    appid: str
    secret: str
    openid: str = ""  # 机器人在群里的 openid（<@openid> 提及形态），用于精确识别@我


@dataclass(frozen=True)
class WatchConfig:
    outbox_dir: str
    interval_seconds: int
    data_dir: str
    character_data: str = "/home/admin/xl_updata_server/data/character_data/current.json"
    versions_dir: str = "/home/admin/xl_updata_server/data/versions"


@dataclass(frozen=True)
class TargetConfig:
    group_openids: List[str]
    auto_learn_from_events: bool


@dataclass(frozen=True)
class UploadConfig:
    file_base_url: str


@dataclass(frozen=True)
class MessageConfig:
    template: str


DEFAULT_BILI_MID = 3537105431038140  # 星落官方号（旧版单目标兼容迁移用）


@dataclass(frozen=True)
class BilibiliTarget:
    mid: int
    name: str = ""  # 空则运行时自动解析（acc/info → 视频 author → mid 字符串）
    mode: str = "full"  # full=图文全文+原图/视频通知；notice=都只发一行通知


def _default_bili_targets() -> List[BilibiliTarget]:
    return [BilibiliTarget(mid=DEFAULT_BILI_MID, name="星落官方", mode="full")]


@dataclass(frozen=True)
class BilibiliConfig:
    enabled: bool = True
    interval_seconds: int = 300
    sessdata: str = ""
    targets: List[BilibiliTarget] = field(default_factory=_default_bili_targets)
    # 定时密集检测：每天 burst_times 起持续 burst_window_seconds，期间每 burst_interval_seconds 检测一次
    burst_times: List[str] = field(default_factory=lambda: ["10:00", "17:00"])
    burst_window_seconds: int = 300
    burst_interval_seconds: int = 60
    timezone: str = "Asia/Shanghai"


@dataclass(frozen=True)
class GroupsConfig:
    """群分级 + 功能门禁配置：[groups] 群列表 + [features] 功能所需级别。"""

    debug: List[str] = field(default_factory=list)
    test: List[str] = field(default_factory=list)
    features: Dict[str, str] = field(default_factory=dict)  # 功能名 -> production/test/debug


@dataclass(frozen=True)
class RouterConfig:
    """事件路由器：[router] 各级别服务端口与转发超时。"""

    debug_port: int = 8781
    test_port: int = 8782
    production_port: int = 8783
    forward_timeout: float = 8.0


@dataclass(frozen=True)
class Config:
    bot: BotConfig
    watch: WatchConfig
    target: TargetConfig
    upload: UploadConfig
    message: MessageConfig
    bilibili: BilibiliConfig = field(default_factory=BilibiliConfig)
    groups: GroupsConfig = field(default_factory=GroupsConfig)
    router: RouterConfig = field(default_factory=RouterConfig)


def _load_bili_targets(bili: dict) -> List[BilibiliTarget]:
    raw_targets = bili.get("targets")
    if raw_targets is None:
        # 兼容旧配置：没有 [[bilibili.targets]] 时按旧行为（单目标 full 模式）
        legacy_mid = int(bili.get("mid", DEFAULT_BILI_MID))
        return [BilibiliTarget(mid=legacy_mid, name="星落官方", mode="full")]
    if not isinstance(raw_targets, list):
        raise ValueError("[bilibili.targets] 必须是数组表 [[bilibili.targets]]")

    targets: List[BilibiliTarget] = []
    seen_mids = set()
    for item in raw_targets:
        if not isinstance(item, dict):
            raise ValueError("[bilibili.targets] 条目必须是表")
        raw_mid = str(item.get("mid", "")).strip()
        if not raw_mid:
            raise ValueError("[bilibili.targets] mid 不能为空")
        try:
            mid = int(raw_mid)
        except ValueError:
            raise ValueError(f"[bilibili.targets] mid 不是数字: {raw_mid!r}")
        if mid in seen_mids:
            raise ValueError(f"[bilibili.targets] mid 重复: {mid}")
        seen_mids.add(mid)
        mode = str(item.get("mode", "full")).strip() or "full"
        if mode not in ("full", "notice"):
            raise ValueError(f"[bilibili.targets] mode 只允许 full/notice: {mode!r}")
        targets.append(BilibiliTarget(
            mid=mid,
            name=str(item.get("name", "")).strip(),
            mode=mode,
        ))
    return targets


def load_config(path: str = "config.toml") -> Config:
    if not os.path.exists(path):
        raise FileNotFoundError(f"配置文件不存在: {os.path.abspath(path)}")

    with open(path, "rb") as f:
        raw = tomllib.load(f)

    bot = raw.get("bot", {})
    appid = str(bot.get("appid", "")).strip()
    secret = str(bot.get("secret", "")).strip()
    if not appid:
        raise ValueError("[bot] appid 不能为空，请按 config.toml.example 填写凭据")
    if not secret:
        raise ValueError("[bot] secret 不能为空，请按 config.toml.example 填写凭据")

    watch = raw.get("watch", {})
    outbox_dir = str(watch.get("outbox_dir", "")).strip()
    if not outbox_dir:
        raise ValueError("[watch] outbox_dir 不能为空")
    interval_seconds = int(watch.get("interval_seconds", 30))
    if interval_seconds < 1:
        raise ValueError("[watch] interval_seconds 必须 >= 1")
    data_dir = str(watch.get("data_dir", "./data")).strip()

    target = raw.get("target", {})
    group_openids = list(target.get("group_openids", []))
    auto_learn = bool(target.get("auto_learn_from_events", True))

    upload = raw.get("upload", {})
    file_base_url = str(upload.get("file_base_url", "")).strip()

    message = raw.get("message", {})
    # 留空 = 只发图不配文字
    template = str(message.get("template", "")).strip()

    bili = raw.get("bilibili", {})
    bilibili = BilibiliConfig(
        enabled=bool(bili.get("enabled", True)),
        interval_seconds=int(bili.get("interval_seconds", BilibiliConfig.interval_seconds)),
        sessdata=str(bili.get("sessdata", "")).strip(),
        targets=_load_bili_targets(bili),
        burst_times=[str(t).strip() for t in bili.get("burst_times", ["10:00", "17:00"])],
        burst_window_seconds=int(bili.get("burst_window_seconds", 300)),
        burst_interval_seconds=int(bili.get("burst_interval_seconds", 60)),
        timezone=str(bili.get("timezone", "Asia/Shanghai")).strip(),
    )
    if bilibili.interval_seconds < 1:
        raise ValueError("[bilibili] interval_seconds 必须 >= 1")
    for burst_time in bilibili.burst_times:
        _validate_hhmm(burst_time)
    if bilibili.burst_window_seconds < 1:
        raise ValueError("[bilibili] burst_window_seconds 必须 >= 1")
    if bilibili.burst_interval_seconds < 1:
        raise ValueError("[bilibili] burst_interval_seconds 必须 >= 1")
    try:
        ZoneInfo(bilibili.timezone)
    except ZoneInfoNotFoundError:
        raise ValueError(f"[bilibili] timezone 非法: {bilibili.timezone!r}")

    groups = raw.get("groups", {})
    features_raw = raw.get("features", {})
    if not isinstance(features_raw, dict):
        raise ValueError("[features] 必须是表")
    features: Dict[str, str] = {}
    for name, tier in features_raw.items():
        tier_value = str(tier).strip()
        if tier_value not in ("production", "test", "debug"):
            raise ValueError(
                f"[features] {name} 级别非法: {tier_value!r}（只允许 production/test/debug）"
            )
        features[str(name).strip()] = tier_value
    groups_config = GroupsConfig(
        debug=[str(g).strip() for g in groups.get("debug", []) if str(g).strip()],
        test=[str(g).strip() for g in groups.get("test", []) if str(g).strip()],
        features=features,
    )

    router = raw.get("router", {})
    router_config = RouterConfig(
        debug_port=int(router.get("debug_port", RouterConfig.debug_port)),
        test_port=int(router.get("test_port", RouterConfig.test_port)),
        production_port=int(router.get("production_port", RouterConfig.production_port)),
        forward_timeout=float(router.get("forward_timeout", RouterConfig.forward_timeout)),
    )
    if router_config.forward_timeout <= 0:
        raise ValueError("[router] forward_timeout 必须 > 0")

    return Config(
        bot=BotConfig(appid=appid, secret=secret, openid=str(bot.get("openid", "")).strip()),
        watch=WatchConfig(
            outbox_dir=outbox_dir,
            interval_seconds=interval_seconds,
            data_dir=data_dir,
            character_data=str(watch.get("character_data", WatchConfig.character_data)).strip(),
            versions_dir=str(watch.get("versions_dir", WatchConfig.versions_dir)).strip(),
        ),
        target=TargetConfig(
            group_openids=group_openids,
            auto_learn_from_events=auto_learn,
        ),
        upload=UploadConfig(file_base_url=file_base_url),
        message=MessageConfig(template=template),
        bilibili=bilibili,
        groups=groups_config,
        router=router_config,
    )
