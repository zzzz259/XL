"""群归属路由与各功能不再受 feature-level 配置限制的集成测试。"""

import json
import os
import tempfile

import pytest

from bot_app.bilibili import BilibiliWatcher
from bot_app.config import (
    DEFAULT_BILI_MID,
    BilibiliConfig,
    BilibiliTarget,
    BotConfig,
    Config,
    GroupsConfig,
    MessageConfig,
    TargetConfig,
    UploadConfig,
    WatchConfig,
    load_config,
)
from bot_app.main import _EventClient  # noqa: F401  确认旧入口仍可导入
from bot_app.matcher import CharacterMatcher
from bot_app.querier import CharacterQuerier
from bot_app.query_handler import QueryHandler
from bot_app.selection import SelectionStore
from bot_app.tiers import GroupTier
from bot_app.updater import ServiceMute
from bot_app.watcher import Watcher

DEBUG_G = "7D8F62B840FF97A1E3EF0E219575EFA8"
TEST_G = "C856F085818C52C0E850753B855B3452"
PROD_G = "AAAAF085818C52C0E850753B855B1111"


def make_tiers(debug=None, test=None):
    return GroupTier(
        GroupsConfig(
            debug=debug or [],
            test=test or [],
        )
    )


# ---------- 配置解析 ----------


def write_toml(content):
    with tempfile.NamedTemporaryFile(
        mode="w", suffix=".toml", delete=False, encoding="utf-8"
    ) as f:
        f.write(content)
        return f.name


BASE_TOML = """
[bot]
appid = "123"
secret = "secret"

[watch]
outbox_dir = "/tmp/outbox"
"""


def test_config_groups_defaults():
    path = write_toml(BASE_TOML)
    try:
        cfg = load_config(path)
        assert cfg.groups.debug == [] and cfg.groups.test == []
        assert not hasattr(cfg.groups, "features")
    finally:
        os.unlink(path)


def test_legacy_features_are_ignored_while_group_routing_parses():
    path = write_toml(
        BASE_TOML
        + f"""
[groups]
debug = ["{DEBUG_G}"]
test = ["{TEST_G}"]

[features]
character_query = "debug"
bilibili_watch = "test"
"""
    )
    try:
        cfg = load_config(path)
        assert cfg.groups.debug == [DEBUG_G]
        assert cfg.groups.test == [TEST_G]
        assert not hasattr(cfg.groups, "features")
    finally:
        os.unlink(path)


def test_legacy_unknown_feature_tier_does_not_block_startup():
    path = write_toml(
        BASE_TOML
        + """
[features]
character_query = "verbose"
"""
    )
    try:
        cfg = load_config(path)
        assert not hasattr(cfg.groups, "features")
    finally:
        os.unlink(path)


# ---------- GroupTier 判定 ----------


def test_tier_of_three_levels_and_default():
    tiers = make_tiers(debug=[DEBUG_G], test=[TEST_G])
    assert tiers.tier_of(DEBUG_G) == "debug"
    assert tiers.tier_of(TEST_G) == "test"
    assert tiers.tier_of(PROD_G) == "production"  # 未配置默认正式
    assert tiers.tier_of(None) == "production"


def test_tier_change_takes_effect_immediately():
    """群归属由配置列表决定；功能不再跟随群级别开关。"""
    assert make_tiers(debug=[DEBUG_G]).tier_of(DEBUG_G) == "debug"
    assert make_tiers().tier_of(DEBUG_G) == "production"


# ---------- watcher 集成 ----------


def make_watcher_env(tmp_path, manual_groups, events=None):
    outbox_dir = tmp_path / "outbox"
    data_dir = tmp_path / "data"
    vdir = outbox_dir / "v1"
    vdir.mkdir(parents=True)
    (vdir / "manifest.json").write_text(
        json.dumps(
            {
                "version": "v1",
                "characters": [{"id": "1", "name": "A", "file_name": "1_A.png"}],
            }
        ),
        encoding="utf-8",
    )
    (vdir / "1_A.png").write_bytes(b"png")
    if events:
        events_file = outbox_dir.parent / "update_events.jsonl"
        with open(events_file, "w", encoding="utf-8") as f:
            f.writelines(json.dumps(event) + "\n" for event in events)

    config = Config(
        bot=BotConfig(appid="1", secret="2"),
        watch=WatchConfig(
            outbox_dir=str(outbox_dir), interval_seconds=30, data_dir=str(data_dir)
        ),
        target=TargetConfig(group_openids=manual_groups, auto_learn_from_events=False),
        upload=UploadConfig(file_base_url=""),
        message=MessageConfig(template="T"),
        groups=GroupsConfig(
            debug=[DEBUG_G],
            test=[TEST_G],
        ),
    )
    return config, str(vdir)


class FakeSender:
    def __init__(self):
        self.calls = []

    async def send_image(self, file_path, content, group_openids, reply_to=""):
        self.calls.append(("image", list(group_openids), file_path))
        return {g: True for g in group_openids}

    async def send_text(self, group, text, reply_to=""):
        self.calls.append(("text", group, text))
        return True


@pytest.mark.asyncio
async def test_character_push_is_not_filtered_by_legacy_feature_gate(tmp_path):
    config, vdir = make_watcher_env(tmp_path, manual_groups=[PROD_G, TEST_G])
    sender = FakeSender()
    watcher = Watcher(config, sender, tiers=GroupTier(config.groups))
    await watcher._tick()

    assert sender.calls == []  # 交付由持久 outbox dispatcher 负责
    assert [item.recipient for item in watcher.proactive_outbox.list_pending()] == [PROD_G]
    assert not os.path.exists(vdir)  # main watcher 仅投递至 production 群


@pytest.mark.asyncio
async def test_main_character_push_only_sends_to_production_group(tmp_path):
    config, vdir = make_watcher_env(tmp_path, manual_groups=[PROD_G, DEBUG_G])
    sender = FakeSender()
    watcher = Watcher(config, sender, tiers=GroupTier(config.groups))
    await watcher._tick()

    assert sender.calls == []
    assert [item.recipient for item in watcher.proactive_outbox.list_pending()] == [PROD_G]
    assert not os.path.exists(vdir)  # main watcher 仅投递至 production 群


@pytest.mark.asyncio
async def test_update_notice_is_not_filtered_by_legacy_feature_gate(tmp_path):
    config, _ = make_watcher_env(
        tmp_path,
        manual_groups=[PROD_G, DEBUG_G],
        events=[{"event": "start", "version": 1}],
    )
    sender = FakeSender()
    watcher = Watcher(config, sender, tiers=GroupTier(config.groups))
    await watcher._tick()

    assert sender.calls == []
    queued = watcher.proactive_outbox.list_pending()
    notices = [item for item in queued if item.event_key.startswith("game-update:")]
    assert [(item.recipient, item.kind) for item in notices] == [
        (PROD_G, "text")
    ]
    assert watcher.notice.consumed_events == 1  # 事件已消费不重复播报
    assert watcher.mute.muted  # 播报后进入更新静音（与现有语义一致）


# ---------- bilibili 集成 ----------


def make_bili_watcher(tmp_path, manual_groups):
    data_dir = tmp_path / "data"
    config = Config(
        bot=BotConfig(appid="1", secret="2"),
        watch=WatchConfig(
            outbox_dir="/tmp/outbox", interval_seconds=30, data_dir=str(data_dir)
        ),
        target=TargetConfig(group_openids=manual_groups, auto_learn_from_events=False),
        upload=UploadConfig(file_base_url=""),
        message=MessageConfig(template=""),
        bilibili=BilibiliConfig(
            enabled=True,
            sessdata="s",
            targets=[BilibiliTarget(mid=DEFAULT_BILI_MID, name="X", mode="notice")],
        ),
        groups=GroupsConfig(debug=[DEBUG_G], test=[TEST_G]),
    )
    return BilibiliWatcher(config, FakeSender())


class FakeBiliClient:
    def __init__(self):
        self.feed = [
            {
                "opus_id": 101,
                "content": "公告",
                "cover": {"url": "c"},
                "jump_url": "//x",
            }
        ]

    async def fetch_opus_feed(self, mid):
        return self.feed

    async def fetch_opus_detail(self, opus_id):
        raise AssertionError("notice 路径不应拉详情")

    async def fetch_videos(self, mid):
        return []

    async def fetch_acc_info(self, mid):
        return {"name": "X"}

    async def close(self):
        pass


@pytest.mark.asyncio
async def test_bilibili_watch_is_not_filtered_by_legacy_feature_gate(tmp_path):
    watcher = make_bili_watcher(tmp_path, [PROD_G])
    watcher._client = FakeBiliClient()
    await watcher._tick()  # 基线 101（基线轮不发）
    watcher._client.feed = [
        {"opus_id": 102, "content": "新公告", "cover": {"url": "c"}, "jump_url": "//x"}
    ]
    await watcher._tick()
    state = watcher.state.target(DEFAULT_BILI_MID)
    assert state.last_opus_id == 102
    assert watcher.proactive_outbox.count() > 0


@pytest.mark.asyncio
async def test_bilibili_watch_sends_to_allowed_groups(tmp_path):
    watcher = make_bili_watcher(tmp_path, [PROD_G, DEBUG_G])
    watcher._client = FakeBiliClient()
    await watcher._tick()  # 基线
    watcher._client.feed = [
        {"opus_id": 102, "content": "新公告", "cover": {"url": "c"}, "jump_url": "//x"}
    ]
    await watcher._tick()
    text_groups = [
        item.recipient
        for item in watcher.proactive_outbox.list_pending()
        if item.kind == "text"
    ]
    assert text_groups == [DEBUG_G, PROD_G]
    assert watcher.state.target(DEFAULT_BILI_MID).last_opus_id == 102


# ---------- 消息处理 ----------

CHARACTERS = {"10000101": {"name": "菲尼斯/Finis", "star": 5}}


def make_event_client(tmp_path):
    data_dir = tmp_path / "data"
    char_file = data_dir / "character_data" / "current.json"
    char_file.parent.mkdir(parents=True, exist_ok=True)
    char_file.write_text(
        json.dumps({"characters": CHARACTERS}, ensure_ascii=False), encoding="utf-8"
    )
    versions_dir = data_dir / "versions"
    cards = versions_dir / "123" / "character_cards"
    cards.mkdir(parents=True)
    (cards / "10000101_角色档案_长图.png").write_bytes(b"png")
    (data_dir / "current_version.json").write_text('{"version": 123}', encoding="utf-8")

    sender = FakeSender()
    client = QueryHandler(
        CharacterQuerier(char_file, versions_dir),
        CharacterMatcher(char_file, data_dir),
        SelectionStore(),
        sender,
        bot_openid="",
        mute=ServiceMute(),
    )
    return client, sender


@pytest.mark.asyncio
async def test_query_is_available_in_production_group(tmp_path):
    client, sender = make_event_client(tmp_path)
    await client._answer_query(PROD_G, "菲尼斯", "u1")
    assert [c[0] for c in sender.calls] == ["image"]


@pytest.mark.asyncio
async def test_query_allowed_in_debug_group(tmp_path):
    client, sender = make_event_client(tmp_path)
    await client._answer_query(DEBUG_G, "菲尼斯", "u1")
    assert [c[0] for c in sender.calls] == ["image"]


@pytest.mark.asyncio
async def test_query_is_available_regardless_of_group_tier(tmp_path):
    client, sender = make_event_client(tmp_path)
    await client._answer_query(TEST_G, "菲尼斯", "u1")
    await client._answer_query(DEBUG_G, "菲尼斯", "u1")
    await client._answer_query(PROD_G, "菲尼斯", "u1")
    assert [c[0] for c in sender.calls] == ["image", "image", "image"]


@pytest.mark.asyncio
async def test_selection_without_pending_query_is_not_consumed(tmp_path):
    client, sender = make_event_client(tmp_path)
    handled = await client._handle_selection(PROD_G, "u1", "1")
    assert not handled
    assert sender.calls == []
