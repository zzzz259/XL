import os
import tomllib
from dataclasses import dataclass, field
from typing import List


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


@dataclass(frozen=True)
class Config:
    bot: BotConfig
    watch: WatchConfig
    target: TargetConfig
    upload: UploadConfig
    message: MessageConfig
    bilibili: BilibiliConfig = field(default_factory=BilibiliConfig)


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
    )
    if bilibili.interval_seconds < 1:
        raise ValueError("[bilibili] interval_seconds 必须 >= 1")

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
    )
