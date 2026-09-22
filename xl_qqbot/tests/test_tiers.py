"""群分级 + 功能门禁：判定矩阵、过滤、各消费者集成。"""

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
from bot_app.query_handler import QueryHandler
from bot_app.querier import CharacterQuerier
from bot_app.selection import SelectionStore
from bot_app.tiers import GroupTier
from bot_app.updater import ServiceMute
from bot_app.watcher import Watcher

DEBUG_G = "7D8F62B840FF97A1E3EF0E219575EFA8"
TEST_G = "C856F085818C52C0E850753B855B3452"
PROD_G = "AAAAF085818C52C0E850753B855B1111"


def make_tiers(features=None, debug=None, test=None):
    return GroupTier(GroupsConfig(
        debug=debug or [],
        test=test or [],
        features=features or {},
    ))


# ---------- 配置解析 ----------

def write_toml(content):
    f = tempfile.NamedTemporaryFile(mode="w", suffix=".toml", delete=False, encoding="utf-8")
    f.write(content)
    f.close()
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
        assert cfg.groups.features == {}
    finally:
        os.unlink(path)


def test_config_groups_and_features_parse():
    path = write_toml(BASE_TOML + f"""
[groups]
debug = ["{DEBUG_G}"]
test = ["{TEST_G}"]

[features]
character_query = "debug"
bilibili_watch = "test"
""")
    try:
        cfg = load_config(path)
        assert cfg.groups.debug == [DEBUG_G]
        assert cfg.groups.test == [TEST_G]
        assert cfg.groups.features == {"character_query": "debug", "bilibili_watch": "test"}
    finally:
        os.unlink(path)


def test_config_invalid_feature_tier_raises():
    path = write_toml(BASE_TOML + """
[features]
character_query = "verbose"
""")
    try:
        with pytest.raises(ValueError, match="级别非法"):
            load_config(path)
    finally:
        os.unlink(path)


# ---------- GroupTier 判定 ----------

def test_tier_of_three_levels_and_default():
    tiers = make_tiers(debug=[DEBUG_G], test=[TEST_G])
    assert tiers.tier_of(DEBUG_G) == "debug"
    assert tiers.tier_of(TEST_G) == "test"
    assert tiers.tier_of(PROD_G) == "production"  # 未配置默认正式
    assert tiers.tier_of(None) == "production"


def test_level_of_default_and_unknown_feature():
    tiers = make_tiers(features={"character_query": "debug"})
    assert tiers.level_of("character_query") == "debug"
    assert tiers.level_of("future_feature") == "production"  # 缺省不回归


def test_available_cumulative_matrix():
    tiers = make_tiers(debug=[DEBUG_G], test=[TEST_G],
                       features={"f_debug": "debug", "f_test": "test", "f_prod": "production"})
    # debug 群全开
    assert tiers.available("f_debug", DEBUG_G)
    assert tiers.available("f_test", DEBUG_G)
    assert tiers.available("f_prod", DEBUG_G)
    # test 群：测试+正式，不开调试
    assert not tiers.available("f_debug", TEST_G)
    assert tiers.available("f_test", TEST_G)
    assert tiers.available("f_prod", TEST_G)
    # 正式群：仅正式
    assert not tiers.available("f_debug", PROD_G)
    assert not tiers.available("f_test", PROD_G)
    assert tiers.available("f_prod", PROD_G)


def test_available_default_tiers_all_open():
    tiers = GroupTier()  # 无配置
    assert tiers.available("anything", PROD_G)


def test_filter_groups_preserves_order():
    tiers = make_tiers(debug=[DEBUG_G], features={"character_push": "debug"})
    groups = [PROD_G, DEBUG_G, TEST_G, DEBUG_G]
    assert tiers.filter_groups("character_push", groups) == [DEBUG_G, DEBUG_G]


def test_tier_change_takes_effect_immediately():
    """群从 debug 列表移出立即生效（每次现算）。"""
    tiers = make_tiers(debug=[DEBUG_G], features={"f": "debug"})
    assert tiers.available("f", DEBUG_G)
    tiers = make_tiers(features={"f": "debug"})  # 列表移除
    assert not tiers.available("f", DEBUG_G)


# ---------- watcher 集成 ----------

def make_watcher_env(tmp_path, features, manual_groups, events=None):
    outbox_dir = tmp_path / "outbox"
    data_dir = tmp_path / "data"
    vdir = outbox_dir / "v1"
    vdir.mkdir(parents=True)
    (vdir / "manifest.json").write_text(json.dumps(
        {"version": "v1", "characters": [{"id": "1", "name": "A", "file_name": "1_A.png"}]}
    ), encoding="utf-8")
    (vdir / "1_A.png").write_bytes(b"png")
    if events:
        events_file = outbox_dir.parent / "update_events.jsonl"
        with open(events_file, "w", encoding="utf-8") as f:
            for event in events:
                f.write(json.dumps(event) + "\n")

    config = Config(
        bot=BotConfig(appid="1", secret="2"),
        watch=WatchConfig(outbox_dir=str(outbox_dir), interval_seconds=30, data_dir=str(data_dir)),
        target=TargetConfig(group_openids=manual_groups, auto_learn_from_events=False),
        upload=UploadConfig(file_base_url=""),
        message=MessageConfig(template="T"),
        groups=GroupsConfig(
            debug=[DEBUG_G], test=[TEST_G], features=features,
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
async def test_character_push_filtered_empty_completes_and_removes(tmp_path):
    """正式/测试群被 debug 门禁滤空 → 批次视为完成并删除 outbox，不卡死。"""
    config, vdir = make_watcher_env(
        tmp_path, {"character_push": "debug"}, manual_groups=[PROD_G, TEST_G]
    )
    sender = FakeSender()
    watcher = Watcher(config, sender, tiers=GroupTier(config.groups))
    await watcher._tick()

    assert sender.calls == []  # 没有实际发送
    assert not os.path.exists(vdir)  # 但批次被清理（视为无需发送）


@pytest.mark.asyncio
async def test_character_push_sends_only_to_debug_group(tmp_path):
    config, vdir = make_watcher_env(
        tmp_path, {"character_push": "debug"}, manual_groups=[PROD_G, DEBUG_G]
    )
    sender = FakeSender()
    watcher = Watcher(config, sender, tiers=GroupTier(config.groups))
    await watcher._tick()

    assert sender.calls == [("image", [DEBUG_G], sender.calls[0][2])]
    assert not os.path.exists(vdir)  # debug 群收到即完成


@pytest.mark.asyncio
async def test_update_notice_filtered_per_group(tmp_path):
    """开始/结束播报按 update_notice 门禁过滤；被滤群不播报但事件正常消费。"""
    config, _ = make_watcher_env(
        tmp_path, {"update_notice": "debug"}, manual_groups=[PROD_G, DEBUG_G],
        events=[{"event": "start", "version": 1}],
    )
    sender = FakeSender()
    watcher = Watcher(config, sender, tiers=GroupTier(config.groups))
    await watcher._tick()

    text_groups = [c[1] for c in sender.calls if c[0] == "text"]
    assert text_groups == [DEBUG_G]
    assert watcher.notice.consumed_events == 1  # 事件已消费不重复播报
    assert watcher.mute.muted  # 播报后进入更新静音（与现有语义一致）


# ---------- bilibili 集成 ----------

def make_bili_watcher(tmp_path, features, manual_groups):
    data_dir = tmp_path / "data"
    config = Config(
        bot=BotConfig(appid="1", secret="2"),
        watch=WatchConfig(outbox_dir="/tmp/outbox", interval_seconds=30, data_dir=str(data_dir)),
        target=TargetConfig(group_openids=manual_groups, auto_learn_from_events=False),
        upload=UploadConfig(file_base_url=""),
        message=MessageConfig(template=""),
        bilibili=BilibiliConfig(
            enabled=True, sessdata="s",
            targets=[BilibiliTarget(mid=DEFAULT_BILI_MID, name="X", mode="notice")],
        ),
        groups=GroupsConfig(debug=[DEBUG_G], test=[TEST_G], features=features),
    )
    return BilibiliWatcher(config, FakeSender(), tiers=GroupTier(config.groups))


class FakeBiliClient:
    def __init__(self):
        self.feed = [{"opus_id": 101, "content": "公告", "cover": {"url": "c"},
                      "jump_url": "//x"}]

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
async def test_bilibili_watch_filtered_empty_keeps_state(tmp_path):
    """bilibili_watch 被滤空 → 有新鲜条目也不推进 state（配置变了下轮可重试）。"""
    watcher = make_bili_watcher(tmp_path, {"bilibili_watch": "debug"}, [PROD_G])
    watcher._client = FakeBiliClient()
    await watcher._tick()  # 基线 101（基线轮不发）
    watcher._client.feed = [{"opus_id": 102, "content": "新公告", "cover": {"url": "c"},
                             "jump_url": "//x"}]
    await watcher._tick()
    state = watcher.state.target(DEFAULT_BILI_MID)
    assert state.last_opus_id == 101  # 102 未推进
    assert len(watcher.sender.calls) == 0


@pytest.mark.asyncio
async def test_bilibili_watch_sends_to_allowed_groups(tmp_path):
    watcher = make_bili_watcher(tmp_path, {"bilibili_watch": "production"}, [PROD_G, DEBUG_G])
    watcher._client = FakeBiliClient()
    await watcher._tick()  # 基线
    watcher._client.feed = [{"opus_id": 102, "content": "新公告", "cover": {"url": "c"},
                             "jump_url": "//x"}]
    await watcher._tick()
    text_groups = [c[1] for c in watcher.sender.calls if c[0] == "text"]
    assert text_groups == [DEBUG_G, PROD_G]  # 两级群都开放（production 级）
    assert watcher.state.target(DEFAULT_BILI_MID).last_opus_id == 102


# ---------- 事件处理门禁 ----------

CHARACTERS = {"10000101": {"name": "菲尼斯/Finis", "star": 5}}


def make_event_client(tmp_path, features, debug=None, test=None):
    data_dir = tmp_path / "data"
    char_file = data_dir / "character_data" / "current.json"
    char_file.parent.mkdir(parents=True, exist_ok=True)
    char_file.write_text(json.dumps({"characters": CHARACTERS}, ensure_ascii=False), encoding="utf-8")
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
        tiers=make_tiers(features=features, debug=debug, test=test),
        bot_openid="",
        mute=ServiceMute(),
    )
    return client, sender


@pytest.mark.asyncio
async def test_query_gated_in_production_group(tmp_path):
    client, sender = make_event_client(
        tmp_path, {"character_query": "debug"}, debug=[DEBUG_G]
    )
    await client._answer_query(PROD_G, "菲尼斯", "u1")
    assert sender.calls == []  # 静默忽略


@pytest.mark.asyncio
async def test_query_allowed_in_debug_group(tmp_path):
    client, sender = make_event_client(
        tmp_path, {"character_query": "debug"}, debug=[DEBUG_G]
    )
    await client._answer_query(DEBUG_G, "菲尼斯", "u1")
    assert [c[0] for c in sender.calls] == ["image"]


@pytest.mark.asyncio
async def test_query_test_feature_in_test_and_debug_groups(tmp_path):
    client, sender = make_event_client(
        tmp_path, {"character_query": "test"}, debug=[DEBUG_G], test=[TEST_G]
    )
    await client._answer_query(TEST_G, "菲尼斯", "u1")
    await client._answer_query(DEBUG_G, "菲尼斯", "u1")
    await client._answer_query(PROD_G, "菲尼斯", "u1")  # 正式群静默
    assert [c[0] for c in sender.calls] == ["image", "image"]


@pytest.mark.asyncio
async def test_selection_gated_group_consumed_silently(tmp_path):
    client, sender = make_event_client(
        tmp_path, {"character_query": "debug"}, debug=[DEBUG_G]
    )
    handled = await client._handle_selection(PROD_G, "u1", "1")
    assert handled  # 门禁外也消费数字消息（静默），不落入后续流程
    assert sender.calls == []


@pytest.mark.asyncio
async def test_pending_created_then_group_downgraded(tmp_path):
    """群降级后：新查询被门禁拦截，旧待选择也被门禁挡住（不再响应）。"""
    client, sender = make_event_client(
        tmp_path, {"character_query": "production"}, debug=[DEBUG_G]
    )
    await client._answer_query(DEBUG_G, "菲尼斯", "u1")
    assert [c[0] for c in sender.calls] == ["image"]
    # 群从 debug 移到正式（feature=debug 时）：用 debug 级 feature 模拟降级
    client._tiers = make_tiers(features={"character_query": "debug"}, debug=[])
    await client._answer_query(DEBUG_G, "菲尼斯", "u1")
    assert len(sender.calls) == 1  # 无新增发送
    handled = await client._handle_selection(DEBUG_G, "u1", "1")
    assert handled and len(sender.calls) == 1
