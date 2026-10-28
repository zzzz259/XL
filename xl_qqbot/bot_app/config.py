import os
import re
import tomllib
from dataclasses import dataclass, field
from pathlib import Path
from typing import List
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
class UpdateSourceConfig:
    name: str
    outbox_dir: str
    minimum_tier: str = "production"


@dataclass(frozen=True)
class WatchConfig:
    outbox_dir: str
    interval_seconds: int
    data_dir: str
    character_data: str = "/home/admin/xl_updata_server/data/character_data/current.json"
    versions_dir: str = "/home/admin/xl_updata_server/data/versions"
    update_sources: tuple[UpdateSourceConfig, ...] = ()


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
    """Router 用于把群分配到对应运行环境的群列表。"""

    debug: List[str] = field(default_factory=list)
    test: List[str] = field(default_factory=list)


@dataclass(frozen=True)
class InstanceConfig:
    instance_id: str = "default"


@dataclass(frozen=True)
class RouterConfig:
    """事件路由器：[router] 各级别服务端口与转发超时。"""

    debug_port: int = 8781
    test_port: int = 8782
    production_port: int = 8783
    forward_timeout: float = 8.0
    deployment_control_port: int = 8784
    deployment_token: str = ""
    group_instances: dict[str, str] = field(default_factory=dict)
    instance_ports: dict[str, int] = field(default_factory=dict)
    group_routing_configured: bool = False


@dataclass(frozen=True)
class NotificationConfig:
    """Ports and shared bearer credential for instance notification APIs."""

    api_token: str = ""
    test_port: int = 8786
    production_port: int = 8787
    instance_ports: dict[str, int] = field(default_factory=dict)


@dataclass(frozen=True)
class GatewayConfig:
    """Gateway-only internal coordination API settings."""

    control_port: int = 8785
    control_token: str = ""
    instance_tokens: dict[str, str] = field(default_factory=dict, repr=False)


@dataclass(frozen=True)
class ServerStatusConfig:
    enabled: bool = False
    interval_seconds: int = 30
    confirm_delay_seconds: int = 3
    credentials_file: str = ""
    refresh_enabled: bool = False
    refresh_proxy: str = "direct"
    recipient_tiers: tuple[str, ...] = ("debug", "test")


@dataclass(frozen=True)
class LeaderboardConfig:
    """Opt-in per-season leaderboard archive job; disabled by default.

    ``scope`` is the operator-verified server/account context label stored
    with every run. Per-mode session budgets are configurable but always
    capped by LEADERBOARD_BUDGET_CAPS.
    """

    enabled: bool = False
    credentials_file: str = ""
    refresh_enabled: bool = False
    refresh_proxy: str = "direct"
    scope: str = ""
    data_dir: str = ""
    request_delay: float = 2.0
    metadata_probe_interval: int = 600
    ranking_max_requests: int = 80
    ranking_max_seconds: int = 300
    ranking_max_pages: int = 35
    ranking_max_entries: int = 1000
    lineup_max_requests: int = 400
    lineup_max_seconds: int = 900
    guild_index_max_requests: int = 200
    guild_index_max_seconds: int = 600


# Hard upper bounds for the configurable per-mode session budgets.
LEADERBOARD_BUDGET_CAPS = {
    "ranking": (200, 900),
    "lineup": (800, 1800),
    "guild_index": (400, 1200),
}
LEADERBOARD_SCOPE_RE = re.compile(r"[A-Za-z0-9_.:-]{1,128}")


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
    notification: NotificationConfig = field(default_factory=NotificationConfig)
    gateway: GatewayConfig = field(default_factory=GatewayConfig)
    server_status: ServerStatusConfig = field(default_factory=ServerStatusConfig)
    instance: InstanceConfig = field(default_factory=InstanceConfig)
    leaderboard: LeaderboardConfig = field(default_factory=LeaderboardConfig)


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

    instance_raw = raw.get("instance", {})
    instance_id = str(instance_raw.get("id", "default")).strip()
    if not re.fullmatch(r"[a-z0-9][a-z0-9_-]{0,63}", instance_id):
        raise ValueError("[instance] id must be a lowercase identifier")

    bot = raw.get("bot", {})
    appid = str(bot.get("appid", "")).strip()
    secret = str(bot.get("secret", "")).strip()

    watch = raw.get("watch", {})
    outbox_dir = str(watch.get("outbox_dir", "")).strip()
    if not outbox_dir:
        raise ValueError("[watch] outbox_dir 不能为空")
    interval_seconds = int(watch.get("interval_seconds", 30))
    if interval_seconds < 1:
        raise ValueError("[watch] interval_seconds 必须 >= 1")
    data_dir = str(watch.get("data_dir", "./data")).strip()
    raw_sources = watch.get("update_sources")
    if raw_sources is None:
        update_sources = (
            UpdateSourceConfig(name="main", outbox_dir=outbox_dir),
        )
    else:
        if not isinstance(raw_sources, list) or not raw_sources:
            raise ValueError("[watch.update_sources] 至少需要一个来源条目")
        parsed_sources = []
        seen_source_names = set()
        for item in raw_sources:
            if not isinstance(item, dict):
                raise ValueError("[[watch.update_sources]] 条目必须是表")
            name = str(item.get("name", "")).strip()
            source_outbox = str(item.get("outbox_dir", "")).strip()
            minimum_tier = str(item.get("minimum_tier", "production")).strip()
            if not name or not re.fullmatch(r"[a-z0-9_-]+", name):
                raise ValueError(f"[watch.update_sources] name 非法: {name!r}")
            if name in seen_source_names:
                raise ValueError(f"[watch.update_sources] name 重复: {name!r}")
            if not source_outbox:
                raise ValueError(f"[watch.update_sources.{name}] outbox_dir 不能为空")
            if minimum_tier not in ("production", "test", "debug"):
                raise ValueError(
                    f"[watch.update_sources.{name}] minimum_tier 非法: {minimum_tier!r}"
                )
            seen_source_names.add(name)
            parsed_sources.append(
                UpdateSourceConfig(name, source_outbox, minimum_tier)
            )
        if "main" not in seen_source_names:
            raise ValueError("[watch.update_sources] 必须包含 main 来源")
        update_sources = tuple(parsed_sources)
        main_source = next(source for source in update_sources if source.name == "main")
        if main_source.outbox_dir != outbox_dir:
            raise ValueError(
                "[watch.update_sources.main] outbox_dir 必须与 [watch] outbox_dir 一致"
            )

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
    groups_config = GroupsConfig(
        debug=[str(g).strip() for g in groups.get("debug", []) if str(g).strip()],
        test=[str(g).strip() for g in groups.get("test", []) if str(g).strip()],
    )

    router = raw.get("router", {})
    raw_group_instances = router.get("group_instances", {})
    raw_instance_ports = router.get("instance_ports", {})
    if not isinstance(raw_group_instances, dict):
        raise ValueError("[router.group_instances] must be a table")
    if not isinstance(raw_instance_ports, dict):
        raise ValueError("[router.instance_ports] must be a table")
    group_instances = {
        str(group).strip(): str(target_instance).strip()
        for group, target_instance in raw_group_instances.items()
    }
    if len(group_instances) != len(raw_group_instances):
        raise ValueError("[router.group_instances] group IDs must be unique")
    if any(not group or not target for group, target in group_instances.items()):
        raise ValueError("[router.group_instances] group and instance IDs cannot be empty")
    instance_ports: dict[str, int] = {}
    for raw_id, raw_port in raw_instance_ports.items():
        target_instance = str(raw_id).strip()
        if not re.fullmatch(r"[a-z0-9][a-z0-9_-]{0,63}", target_instance):
            raise ValueError(f"[router.instance_ports] invalid instance ID: {target_instance!r}")
        if isinstance(raw_port, bool) or not isinstance(raw_port, int):
            raise ValueError(f"[router.instance_ports.{target_instance}] port must be an integer")
        if not 1 <= raw_port <= 65535:
            raise ValueError(f"[router.instance_ports.{target_instance}] port is out of range")
        instance_ports[target_instance] = raw_port
    if len(set(instance_ports.values())) != len(instance_ports):
        raise ValueError("[router.instance_ports] ports must be unique")
    unknown_instances = set(group_instances.values()) - set(instance_ports)
    if unknown_instances:
        raise ValueError(
            f"[router.group_instances] unknown instance without a worker: {sorted(unknown_instances)!r}"
        )
    router_config = RouterConfig(
        debug_port=int(router.get("debug_port", RouterConfig.debug_port)),
        test_port=int(router.get("test_port", RouterConfig.test_port)),
        production_port=int(router.get("production_port", RouterConfig.production_port)),
        forward_timeout=float(router.get("forward_timeout", RouterConfig.forward_timeout)),
        deployment_control_port=int(
            router.get("deployment_control_port", RouterConfig.deployment_control_port)
        ),
        deployment_token=str(router.get("deployment_token", "")).strip(),
        group_instances=group_instances,
        instance_ports=instance_ports,
        group_routing_configured=(
            "group_instances" in router or "instance_ports" in router
        ),
    )
    if router_config.forward_timeout <= 0:
        raise ValueError("[router] forward_timeout 必须 > 0")
    if router_config.deployment_control_port < 1 or router_config.deployment_control_port > 65535:
        raise ValueError("[router] deployment_control_port 必须在 1 到 65535 之间")
    service_ports = {
        router_config.debug_port,
        router_config.test_port,
        router_config.production_port,
    }
    if router_config.deployment_control_port in service_ports:
        raise ValueError("[router] deployment_control_port 不能与服务端口重复")
    if router_config.deployment_token and len(router_config.deployment_token) < 32:
        raise ValueError("[router] deployment_token 必须至少 32 个字符")

    notification_raw = raw.get("notification", {})
    notification_instances = notification_raw.get("instance_ports", {})
    if not isinstance(notification_instances, dict):
        raise ValueError("[notification.instance_ports] 必须是表")
    notification_instance_ports = {
        str(instance_id).strip(): int(port)
        for instance_id, port in notification_instances.items()
    }
    if any(
        not re.fullmatch(r"[a-z0-9][a-z0-9_-]{0,63}", instance_id)
        or not 1 <= port <= 65535
        for instance_id, port in notification_instance_ports.items()
    ):
        raise ValueError("[notification.instance_ports] 实例 ID 或端口无效")
    notification_config = NotificationConfig(
        api_token=str(notification_raw.get("api_token", "")).strip(),
        test_port=int(notification_raw.get("test_port", NotificationConfig.test_port)),
        production_port=int(
            notification_raw.get(
                "production_port", NotificationConfig.production_port
            )
        ),
        instance_ports=notification_instance_ports,
    )
    notification_ports = {
        notification_config.test_port,
        notification_config.production_port,
        *notification_config.instance_ports.values(),
    }
    if any(port < 1 or port > 65535 for port in notification_ports):
        raise ValueError("[notification] 端口必须在 1 到 65535 之间")
    reserved_ports = service_ports | {router_config.deployment_control_port, 8785}
    if len(notification_ports) != 2 + len(notification_config.instance_ports) or notification_ports & reserved_ports:
        raise ValueError("[notification] 端口不能与 Gateway、Router 或 worker 服务端口重复")
    if notification_config.api_token and len(notification_config.api_token) < 32:
        raise ValueError("[notification] api_token 必须至少 32 个字符")

    gateway_raw = raw.get("gateway", {})
    gateway_config = GatewayConfig(
        control_port=int(gateway_raw.get("control_port", GatewayConfig.control_port)),
        control_token=str(gateway_raw.get("control_token", "")).strip(),
        instance_tokens={
            str(instance_id).strip(): str(token).strip()
            for instance_id, token in dict(gateway_raw.get("instance_tokens", {})).items()
        },
    )
    if gateway_config.control_port < 1 or gateway_config.control_port > 65535:
        raise ValueError("[gateway] control_port 必须在 1 到 65535 之间")
    occupied_ports = service_ports | {router_config.deployment_control_port} | notification_ports
    if gateway_config.control_port in occupied_ports:
        raise ValueError("[gateway] control_port 不能与其他服务端口重复")
    if gateway_config.control_token and len(gateway_config.control_token) < 32:
        raise ValueError("[gateway] control_token 必须至少 32 个字符")
    if any(
        not re.fullmatch(r"[a-z0-9][a-z0-9_-]{0,63}", instance_id)
        or len(token) < 32
        for instance_id, token in gateway_config.instance_tokens.items()
    ):
        raise ValueError("[gateway.instance_tokens] 实例 ID 或 token 无效")
    if len(set(gateway_config.instance_tokens.values())) != len(gateway_config.instance_tokens):
        raise ValueError("[gateway.instance_tokens] 每个实例必须使用不同 token")
    if gateway_config.control_token in gateway_config.instance_tokens.values():
        raise ValueError("[gateway.instance_tokens] 不能复用 Gateway 控制 token")
    if set(gateway_config.instance_tokens) - set(router_config.instance_ports):
        raise ValueError("[gateway.instance_tokens] 必须对应已配置 worker 实例")
    if router_config.group_routing_configured and set(router_config.instance_ports) - set(gateway_config.instance_tokens):
        raise ValueError("[gateway.instance_tokens] 显式群路由要求每个实例配置独立 token")
    internal_tokens = tuple(
        token
        for token in (
            router_config.deployment_token,
            gateway_config.control_token,
            notification_config.api_token,
        )
        if token
    )
    if len(set(internal_tokens)) > 1:
        raise ValueError("internal API bearer tokens must match")

    status_raw = raw.get("server_status", {})
    raw_tiers = status_raw.get("recipient_tiers", ["debug", "test"])
    if not isinstance(raw_tiers, list):
        raise ValueError("[server_status] recipient_tiers 必须是数组")
    status_tiers = tuple(dict.fromkeys(str(tier).strip() for tier in raw_tiers))
    status_interval = int(status_raw.get("interval_seconds", 30))
    status_confirm_delay = int(status_raw.get("confirm_delay_seconds", 3))
    if status_interval < 1:
        raise ValueError("[server_status] interval_seconds 必须 >= 1")
    if status_confirm_delay < 1 or status_confirm_delay >= status_interval:
        raise ValueError(
            "[server_status] confirm_delay_seconds 必须 >= 1 且小于 interval_seconds"
        )
    if not isinstance(status_raw.get("enabled", False), bool):
        raise ValueError("[server_status] enabled 必须是布尔值")
    credentials_file = str(status_raw.get("credentials_file", "")).strip()
    if status_raw.get("enabled", False) and not credentials_file:
        raise ValueError("[server_status] enabled=true 时必须配置 credentials_file")
    refresh_enabled = status_raw.get("refresh_enabled", False)
    if not isinstance(refresh_enabled, bool):
        raise ValueError("[server_status] refresh_enabled must be boolean")
    refresh_proxy = status_raw.get("refresh_proxy", "direct")
    if not isinstance(refresh_proxy, str):
        raise ValueError("[server_status] refresh_proxy must be direct or a loopback HTTP proxy")
    if refresh_enabled:
        from starfall_refresh.http import HTTP

        HTTP(refresh_proxy)  # Validate transport without networking.
    server_status = ServerStatusConfig(
        enabled=status_raw.get("enabled", False),
        interval_seconds=status_interval,
        confirm_delay_seconds=status_confirm_delay,
        credentials_file=credentials_file,
        refresh_enabled=refresh_enabled,
        refresh_proxy=refresh_proxy,
        recipient_tiers=status_tiers,
    )

    leaderboard_raw = raw.get("leaderboard", {})
    if not isinstance(leaderboard_raw.get("enabled", False), bool):
        raise ValueError("[leaderboard] enabled 必须是布尔值")
    lb_enabled = leaderboard_raw.get("enabled", False)
    lb_credentials = str(leaderboard_raw.get("credentials_file", "")).strip()
    lb_scope = str(leaderboard_raw.get("scope", "")).strip()
    if lb_enabled:
        if not lb_credentials:
            raise ValueError("[leaderboard] enabled=true 时必须配置 credentials_file")
        if not LEADERBOARD_SCOPE_RE.fullmatch(lb_scope):
            raise ValueError("[leaderboard] scope 必须是 1-128 位 [A-Za-z0-9_.:-]")
    lb_refresh_enabled = leaderboard_raw.get("refresh_enabled", False)
    if not isinstance(lb_refresh_enabled, bool):
        raise ValueError("[leaderboard] refresh_enabled must be boolean")
    lb_refresh_proxy = leaderboard_raw.get("refresh_proxy", "direct")
    if not isinstance(lb_refresh_proxy, str):
        raise ValueError("[leaderboard] refresh_proxy must be direct or a loopback HTTP proxy")
    if lb_refresh_enabled:
        from starfall_refresh.http import HTTP

        HTTP(lb_refresh_proxy)  # Validate transport without networking.
    lb_request_delay = float(leaderboard_raw.get("request_delay", 2.0))
    if lb_request_delay <= 0 or lb_request_delay > 60:
        raise ValueError("[leaderboard] request_delay 必须 > 0 且 <= 60")
    lb_probe_interval = int(leaderboard_raw.get("metadata_probe_interval", 600))
    if lb_probe_interval < 60 or lb_probe_interval > 86400:
        raise ValueError("[leaderboard] metadata_probe_interval 必须在 60 到 86400 秒之间")
    lb_budgets = {}
    for key, default, cap in (
        ("ranking_max_requests", 80, LEADERBOARD_BUDGET_CAPS["ranking"][0]),
        ("ranking_max_seconds", 300, LEADERBOARD_BUDGET_CAPS["ranking"][1]),
        ("ranking_max_pages", 35, 60),
        ("ranking_max_entries", 1000, 1000),
        ("lineup_max_requests", 400, LEADERBOARD_BUDGET_CAPS["lineup"][0]),
        ("lineup_max_seconds", 900, LEADERBOARD_BUDGET_CAPS["lineup"][1]),
        ("guild_index_max_requests", 200, LEADERBOARD_BUDGET_CAPS["guild_index"][0]),
        ("guild_index_max_seconds", 600, LEADERBOARD_BUDGET_CAPS["guild_index"][1]),
    ):
        value = int(leaderboard_raw.get(key, default))
        if value < 1 or value > cap:
            raise ValueError(f"[leaderboard] {key} 必须在 1 到 {cap} 之间")
        lb_budgets[key] = value
    lb_data_dir = str(leaderboard_raw.get("data_dir", "")).strip()
    if not lb_data_dir:
        lb_data_dir = str(Path(data_dir) / "leaderboard")
    leaderboard = LeaderboardConfig(
        enabled=lb_enabled,
        credentials_file=lb_credentials,
        refresh_enabled=lb_refresh_enabled,
        refresh_proxy=lb_refresh_proxy,
        scope=lb_scope,
        data_dir=lb_data_dir,
        request_delay=lb_request_delay,
        metadata_probe_interval=lb_probe_interval,
        **lb_budgets,
    )

    return Config(
        bot=BotConfig(appid=appid, secret=secret, openid=str(bot.get("openid", "")).strip()),
        watch=WatchConfig(
            outbox_dir=outbox_dir,
            interval_seconds=interval_seconds,
            data_dir=data_dir,
            character_data=str(watch.get("character_data", WatchConfig.character_data)).strip(),
            versions_dir=str(watch.get("versions_dir", WatchConfig.versions_dir)).strip(),
            update_sources=update_sources,
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
        notification=notification_config,
        gateway=gateway_config,
        server_status=server_status,
        instance=InstanceConfig(instance_id=instance_id),
        leaderboard=leaderboard,
    )


def require_gateway_credentials(config: Config) -> None:
    """Require the QQ application credential only in the Gateway process."""
    if not config.bot.appid or not config.bot.secret:
        raise ValueError("Gateway requires [bot] appid and secret credentials")
