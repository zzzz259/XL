import asyncio
import json
import os
import tempfile
import urllib.parse

import pytest

import bot_app.bilibili as bilibili
from bot_app.bilibili import (
    BilibiliApiError,
    BilibiliClient,
    BilibiliStateStore,
    BilibiliWatcher,
    _mixin_key,
    format_notice_text,
    format_opus_text,
    parse_opus_detail,
    parse_opus_feed_item,
    parse_video_item,
)
from bot_app.config import (
    DEFAULT_BILI_MID,
    BilibiliConfig,
    BilibiliTarget,
    BotConfig,
    Config,
    MessageConfig,
    TargetConfig,
    UploadConfig,
    WatchConfig,
    load_config,
)

MID = DEFAULT_BILI_MID  # 3537105431038140 星落官方
UP1 = 11310554
UP2 = 1623229430


# ---------- fixtures（按实测返回结构构造） ----------

def feed_item(opus_id, content="标题"):
    return {
        "opus_id": opus_id,
        "content": content,
        "cover": {"url": f"https://i0.hdslb.com/cover/{opus_id}.jpg"},
        "jump_url": f"//www.bilibili.com/opus/{opus_id}",
        "pub_time": "",
    }


def text_para(*words):
    return {"para_type": 1, "text": {"nodes": [{"word": {"words": w}} for w in words]}}


def pic_para(*urls):
    return {"para_type": 2, "pic": {"pics": [{"url": u} for u in urls]}}


def detail_item(opus_id="1001", pub_ts="1700000100", title="维护公告",
                paragraphs=(), dtype=0, id_str=None):
    return {
        "id_str": id_str if id_str is not None else str(opus_id),
        "type": dtype,
        "modules": [
            {"module_type": "MODULE_TYPE_AUTHOR",
             "module_author": {"name": "星落官方", "pub_ts": pub_ts}},
            {"module_type": "MODULE_TYPE_TITLE", "module_title": {"text": title}},
            {"module_type": "MODULE_TYPE_CONTENT",
             "module_content": {"paragraphs": list(paragraphs)}},
            {"module_type": "MODULE_TYPE_STAT", "module_stat": {"like": 1}},
        ],
    }


def video_item(bvid="BV1xx411c7mD", title="1.0 版本 PV", created=1700000000, author=""):
    item = {"bvid": bvid, "title": title, "created": created}
    if author:
        item["author"] = author
    return item


class FakeSender:
    def __init__(self):
        self.events = []

    async def send_text(self, group, text):
        self.events.append(("text", group, text))
        return True

    async def send_image(self, file_path, content, group_openids):
        self.events.append(("image", file_path, content, list(group_openids)))
        return {g: True for g in group_openids}


class FakeClient:
    """按目标分桶的 fake 客户端。"""

    def __init__(self, feeds=None, details=None, videos=None, acc_names=None,
                 feed_errors=None, videos_errors=None, acc_errors=None, detail_error=None):
        self.feeds = feeds or {}          # mid -> [feed items]
        self.details = details or {}      # opus_id -> detail item
        self.videos = videos or {}        # mid -> [vlist items]
        self.acc_names = acc_names or {}  # mid -> data.name
        self.feed_errors = feed_errors or set()
        self.videos_errors = videos_errors or set()
        self.acc_errors = acc_errors or set()
        self.detail_error = detail_error
        self.feed_calls = []
        self.videos_calls = []
        self.acc_calls = []
        self.detail_calls = []

    async def fetch_opus_feed(self, mid):
        self.feed_calls.append(mid)
        if mid in self.feed_errors:
            raise BilibiliApiError("HTTP 412")
        return self.feeds.get(mid, [])

    async def fetch_opus_detail(self, opus_id):
        self.detail_calls.append(opus_id)
        if self.detail_error:
            raise self.detail_error
        try:
            return self.details[opus_id]
        except KeyError:
            raise BilibiliApiError(f"opus 详情 404: {opus_id}")

    async def fetch_videos(self, mid):
        self.videos_calls.append(mid)
        if mid in self.videos_errors:
            raise BilibiliApiError("code=-352")
        return self.videos.get(mid, [])

    async def fetch_acc_info(self, mid):
        self.acc_calls.append(mid)
        if mid in self.acc_errors:
            raise BilibiliApiError("acc/info 412")
        if mid not in self.acc_names:
            raise BilibiliApiError("acc/info -404")
        return {"name": self.acc_names[mid]}

    async def download_image(self, url, dest_dir, name="image", mid=None):
        os.makedirs(dest_dir, exist_ok=True)
        path = os.path.join(dest_dir, f"{name}.png")
        with open(path, "wb") as f:
            f.write(b"fake-png")
        return path

    async def close(self):
        pass


def make_config(data_dir, *, groups=("g1", "g2"), targets=None, bili_kwargs=None):
    bili = {"enabled": True, "interval_seconds": 300}
    bili.update(bili_kwargs or {})
    if targets is not None:
        bili["targets"] = targets
    return Config(
        bot=BotConfig(appid="1", secret="2"),
        watch=WatchConfig(outbox_dir="/tmp/outbox", interval_seconds=30, data_dir=data_dir),
        target=TargetConfig(group_openids=list(groups), auto_learn_from_events=False),
        upload=UploadConfig(file_base_url=""),
        message=MessageConfig(template=""),
        bilibili=BilibiliConfig(**bili),
    )


def make_watcher(data_dir, client, sender=None, *, groups=("g1", "g2"), targets=None):
    watcher = BilibiliWatcher(
        make_config(data_dir, groups=groups, targets=targets), sender or FakeSender()
    )
    watcher._client = client
    return watcher


def write_toml(content):
    f = tempfile.NamedTemporaryFile(mode="w", suffix=".toml", delete=False, encoding="utf-8")
    f.write(content)
    f.close()
    return f.name


def default_targets():
    return [BilibiliTarget(mid=MID, name="星落官方", mode="full")]


# ---------- parse_opus_feed_item ----------

def test_parse_feed_item_ok():
    entry = parse_opus_feed_item(feed_item(1234567890123456789, "新版本"))
    assert entry["opus_id"] == 1234567890123456789
    assert entry["title"] == "新版本"
    assert entry["jump_url"] == "//www.bilibili.com/opus/1234567890123456789"


def test_parse_feed_item_string_opus_id_coerced_to_int():
    item = feed_item(100)
    item["opus_id"] = "100"
    assert parse_opus_feed_item(item)["opus_id"] == 100


def test_parse_feed_item_bad_skipped():
    item = feed_item(100)
    del item["opus_id"]
    assert parse_opus_feed_item(item) is None
    assert parse_opus_feed_item(None) is None
    assert parse_opus_feed_item("junk") is None


# ---------- parse_opus_detail ----------

def test_parse_detail_full():
    item = detail_item(
        "1001", "1700000100", "维护公告",
        [text_para("大家好", "，本周维护"), text_para("第二条"),
         pic_para("https://i0.hdslb.com/p1.jpg", "https://i0.hdslb.com/p2.jpg")],
    )
    dyn = parse_opus_detail(item, opus_id=1001)
    assert dyn["id_str"] == "1001"
    assert dyn["text"] == "维护公告\n大家好，本周维护\n第二条"
    assert dyn["pics"] == ["https://i0.hdslb.com/p1.jpg", "https://i0.hdslb.com/p2.jpg"]
    assert dyn["pub_ts"] == 1700000100
    assert dyn["link"] == "https://www.bilibili.com/opus/1001"


def test_parse_detail_defensive_garbage():
    assert parse_opus_detail(None) is None
    assert parse_opus_detail({"id_str": "1", "type": 0}) is None  # modules 缺失
    assert parse_opus_detail(detail_item("9", dtype=1)) is None   # 非图文
    item = detail_item("1006", paragraphs=[None, "junk", {"para_type": 1}, {"para_type": 2}])
    dyn = parse_opus_detail(item)
    assert dyn is not None and dyn["pics"] == []


# ---------- parse_video_item ----------

def test_parse_video_item_ok_and_bad():
    assert parse_video_item(video_item("BV9", "新 PV", 1700000300)) == {
        "bvid": "BV9", "title": "新 PV", "created": 1700000300,
    }
    assert parse_video_item({"title": "无", "created": 1}) is None
    assert parse_video_item("junk") is None


# ---------- WBI 签名（回归保留） ----------

def test_mixin_key_uses_mixin_key_tab():
    orig = "".join(chr(0x21 + i) for i in range(64))
    expected = "".join(orig[i] for i in bilibili.MixinKeyTab)[:32]
    assert _mixin_key(orig) == expected


def test_mixin_key_fixed_vector():
    assert _mixin_key("7cd93d052cfacdfdf227d66c03c87f54" + "9f6c0dc1bd875914a3d3be45b5e1c6e5") == \
        "142de2c9ddf4e99c87d3fc87f7f1ccdd"


@pytest.mark.asyncio
async def test_signed_params_sorted_with_wts_and_w_rid(monkeypatch):
    client = BilibiliClient(sessdata="s3ss")
    monkeypatch.setattr(bilibili, "_now", lambda: 1700000000)

    async def fake_keys():
        return ("7cd93d052cfacdfdf227d66c03c87f54", "9f6c0dc1bd875914a3d3be45b5e1c6e5")

    client._wbi_key_pair = fake_keys
    signed = await client._signed_params({"mid": str(MID), "ps": "5"})
    assert signed["wts"] == 1700000000
    assert signed["w_rid"] == "e841bfb224ca294145d04934d2ce3ea1"


def test_client_headers_mobile_per_mid():
    client = BilibiliClient(sessdata="abc123")
    headers = client._headers(MID)
    assert headers["Cookie"] == "SESSDATA=abc123"
    assert headers["Referer"] == f"https://m.bilibili.com/space/{MID}"
    assert "Android" in headers["User-Agent"]
    # 无 mid（如下载图片可不携带）与空 cookie
    assert "Referer" not in client._headers(None)
    assert "Cookie" not in BilibiliClient(sessdata="")._headers(MID)


class FakeResponse:
    def __init__(self, status=200, body="{}"):
        self.status = status
        self._body = body

    async def __aenter__(self):
        return self

    async def __aexit__(self, *args):
        return False

    async def text(self):
        return self._body


class FakeSession:
    def __init__(self, response):
        self.response = response
        self.requests = []

    def get(self, url, params=None, headers=None):
        self.requests.append((url, params, headers))
        return self.response


@pytest.mark.asyncio
async def test_fetch_opus_feed_ok():
    payload = {"code": 0, "data": {"items": [feed_item(1)]}}
    session = FakeSession(FakeResponse(200, json.dumps(payload)))
    client = BilibiliClient(sessdata="s", session=session)
    items = await client.fetch_opus_feed(MID)
    assert len(items) == 1
    url, params, headers = session.requests[0]
    assert url == bilibili.OPUS_FEED_URL
    assert params == {"host_mid": str(MID)}  # 无需签名
    assert headers["Referer"] == f"https://m.bilibili.com/space/{MID}"


@pytest.mark.asyncio
async def test_fetch_videos_and_acc_info_use_wbi_signing():
    payload = {"code": 0, "data": {"list": {"vlist": [video_item()]}}}
    session = FakeSession(FakeResponse(200, json.dumps(payload)))
    client = BilibiliClient(session=session)

    async def fake_keys():
        return ("k" * 32, "s" * 32)

    client._wbi_key_pair = fake_keys
    videos = await client.fetch_videos(MID)
    assert len(videos) == 1
    url, params, _ = session.requests[0]
    assert url == bilibili.ARC_SEARCH_URL
    assert params["mid"] == str(MID) and params["ps"] == "5"
    assert "wts" in params and "w_rid" in params

    session2 = FakeSession(FakeResponse(200, json.dumps({"code": 0, "data": {"name": "某某UP"}})))
    client2 = BilibiliClient(session=session2)
    client2._wbi_key_pair = fake_keys
    info = await client2.fetch_acc_info(UP1)
    assert info["name"] == "某某UP"
    url2, params2, _ = session2.requests[0]
    assert url2 == bilibili.ACC_INFO_URL
    assert params2["mid"] == str(UP1) and "w_rid" in params2


@pytest.mark.asyncio
async def test_fetch_412_and_nonzero_code_raise():
    session = FakeSession(FakeResponse(412, "<html>风控</html>"))
    with pytest.raises(BilibiliApiError, match="412"):
        await BilibiliClient(session=session).fetch_opus_feed(MID)

    session2 = FakeSession(FakeResponse(200, json.dumps({"code": -352, "message": "风控"})))
    with pytest.raises(BilibiliApiError, match="-352"):
        await BilibiliClient(session=session2).fetch_videos(MID)


# ---------- 状态：分目标、迁移、名字缓存 ----------

def test_state_per_target_isolated():
    with tempfile.TemporaryDirectory() as root:
        store = BilibiliStateStore(root)
        official = store.target(MID)
        up1 = store.target(UP1)
        assert not official.has_opus_baseline and not up1.has_opus_baseline
        official.mark_opus(100)
        official.mark_video(1000, "BV0")
        up1.mark_opus(50)
        # 互不干扰
        assert store.target(MID).last_opus_id == 100
        assert store.target(UP1).last_opus_id == 50
        assert not store.target(UP1).has_video_baseline
        # 落盘后重载
        reloaded = BilibiliStateStore(root)
        assert reloaded.target(MID).last_bvid == "BV0"
        assert reloaded.target(UP1).last_opus_id == 50


def test_state_file_schema():
    with tempfile.TemporaryDirectory() as root:
        store = BilibiliStateStore(root)
        store.target(MID).mark_opus(7)
        store.target(MID).set_name("星落官方")
        with open(os.path.join(root, "bilibili_state.json"), encoding="utf-8") as f:
            data = json.load(f)
        assert set(data.keys()) == {"targets"}
        entry = data["targets"][str(MID)]
        assert entry["last_opus_id"] == 7 and entry["name"] == "星落官方"


def test_state_migration_from_legacy_top_level():
    with tempfile.TemporaryDirectory() as root:
        path = os.path.join(root, "bilibili_state.json")
        with open(path, "w", encoding="utf-8") as f:
            json.dump({"last_opus_id": 100, "last_video_created": 1000,
                       "last_bvid": "BV0", "last_id_str": "9"}, f)
        store = BilibiliStateStore(root)
        migrated = store.target(MID)
        assert migrated.last_opus_id == 100
        assert migrated.last_video_created == 1000
        assert migrated.last_bvid == "BV0"
        assert migrated.has_opus_baseline and migrated.has_video_baseline
        # 已落盘为新结构，旧顶层键清除
        with open(path, encoding="utf-8") as f:
            data = json.load(f)
        assert "last_opus_id" not in data
        assert set(data["targets"].keys()) == {str(MID)}


def test_state_migration_ignores_very_old_keys():
    with tempfile.TemporaryDirectory() as root:
        path = os.path.join(root, "bilibili_state.json")
        with open(path, "w", encoding="utf-8") as f:
            json.dump({"last_id_str": "123", "last_pub_ts": 1}, f)
        store = BilibiliStateStore(root)
        assert not store.target(MID).has_opus_baseline


def test_state_name_cache_roundtrip():
    with tempfile.TemporaryDirectory() as root:
        store = BilibiliStateStore(root)
        store.target(UP1).set_name("老番茄")
        assert BilibiliStateStore(root).target(UP1).name == "老番茄"


# ---------- watcher：基线与多目标 ----------

@pytest.mark.asyncio
async def test_first_run_baselines_every_target_and_track():
    with tempfile.TemporaryDirectory() as root:
        client = FakeClient(
            feeds={MID: [feed_item(101), feed_item(100)], UP1: [feed_item(55)]},
            videos={MID: [video_item("BV0", "基线", 1000)], UP1: [video_item("BVup", "up基线", 500)]},
        )
        sender = FakeSender()
        watcher = make_watcher(root, client, sender, targets=default_targets() + [
            BilibiliTarget(mid=UP1, name="", mode="notice"),
        ])
        await watcher._tick()

        assert sender.events == []
        assert watcher.state.target(MID).last_opus_id == 101
        assert watcher.state.target(UP1).last_opus_id == 55
        assert watcher.state.target(MID).last_bvid == "BV0"
        assert watcher.state.target(UP1).last_bvid == "BVup"
        assert client.detail_calls == []  # 基线轮不拉详情
        assert client.acc_calls == []     # 基线轮不解析名字


# ---------- watcher：full 模式图文 ----------

@pytest.mark.asyncio
async def test_full_opus_sends_text_then_pics():
    with tempfile.TemporaryDirectory() as root:
        client = FakeClient(feeds={MID: [feed_item(101)]})
        sender = FakeSender()
        watcher = make_watcher(root, client, sender)
        await watcher._tick()  # 基线

        client.feeds[MID] = [feed_item(103), feed_item(102), feed_item(101)]
        client.details = {
            102: detail_item("102", "1700000200", "公告二", [text_para("正文二")]),
            103: detail_item("103", "1700000300", "公告三",
                             [text_para("正文三"), pic_para("https://i0.hdslb.com/p1.jpg")]),
        }
        await watcher._tick()

        assert client.detail_calls == [102, 103]  # 升序拉详情
        kinds = [e[0] for e in sender.events]
        assert kinds == ["text", "text", "text", "text", "image"]
        assert sender.events[0][2] == "【星落官方动态】公告二\n正文二"
        assert sender.events[2][2] == "【星落官方动态】公告三\n正文三"
        img = sender.events[4]
        assert not os.path.exists(img[1])  # 临时图已清理
        assert watcher.state.target(MID).last_opus_id == 103


# ---------- watcher：notice 模式 ----------

@pytest.mark.asyncio
async def test_notice_opus_sends_one_line_no_detail_and_caches_name():
    with tempfile.TemporaryDirectory() as root:
        client = FakeClient(
            feeds={UP1: [feed_item(200, "新视频预告")]},
            acc_names={UP1: "测试UP主"},
        )
        targets = [BilibiliTarget(mid=UP1, name="", mode="notice")]
        sender = FakeSender()
        watcher = make_watcher(root, client, sender, targets=targets)
        await watcher._tick()  # 基线
        assert client.acc_calls == []

        client.feeds[UP1] = [feed_item(202, "二周年贺图"), feed_item(201, "一周年"), feed_item(200, "旧")]
        await watcher._tick()

        assert client.detail_calls == []  # notice 不拉详情
        assert client.acc_calls == [UP1]  # 首次发送时解析名字
        texts = [e[2] for e in sender.events if e[0] == "text" and e[1] == "g1"]
        assert texts == [
            "你关注的测试UP主更新啦：一周年\nhttps://www.bilibili.com/opus/201",
            "你关注的测试UP主更新啦：二周年贺图\nhttps://www.bilibili.com/opus/202",
        ]
        assert watcher.state.target(UP1).last_opus_id == 202
        # 名字已缓存进 state，且下一轮有新内容时不再请求 acc/info
        assert watcher.state.target(UP1).name == "测试UP主"
        client.feeds[UP1] = [feed_item(203, "第三条"), feed_item(202, "旧"), feed_item(201, "旧")]
        await watcher._tick()
        assert client.acc_calls == [UP1]
        assert watcher.state.target(UP1).last_opus_id == 203


@pytest.mark.asyncio
async def test_notice_opus_title_fallback_and_empty_content():
    with tempfile.TemporaryDirectory() as root:
        client = FakeClient(feeds={UP1: [feed_item(300, "")]}, acc_names={UP1: "某UP"})
        targets = [BilibiliTarget(mid=UP1, name="", mode="notice")]
        watcher = make_watcher(root, client, targets=targets)
        await watcher._tick()
        client.feeds[UP1] = [feed_item(301, ""), feed_item(300, "")]
        sender = FakeSender()
        watcher.sender = sender
        await watcher._tick()
        texts = [e[2] for e in sender.events if e[0] == "text" and e[1] == "g1"]
        assert texts == ["你关注的某UP更新啦：新动态\nhttps://www.bilibili.com/opus/301"]


@pytest.mark.asyncio
async def test_full_mode_video_uses_notice_format_with_config_name():
    with tempfile.TemporaryDirectory() as root:
        client = FakeClient(videos={MID: [video_item("BV0", "基线", 1000)]})
        sender = FakeSender()
        watcher = make_watcher(root, client, sender)  # 默认 full 目标 name=星落官方
        await watcher._tick()
        assert client.acc_calls == []  # 配置名非空，不解析

        client.videos[MID] = [video_item("BV2", "二周年 PV", 1700000100), video_item("BV0", "基线", 1000)]
        await watcher._tick()
        assert all(e[0] == "text" for e in sender.events)
        texts = [e[2] for e in sender.events if e[1] == "g1"]
        assert texts == [
            "你关注的星落官方更新啦：二周年 PV\nhttps://www.bilibili.com/video/BV2",
        ]
        assert watcher.state.target(MID).last_bvid == "BV2"


@pytest.mark.asyncio
async def test_notice_video_name_from_vlist_author_when_acc_fails():
    with tempfile.TemporaryDirectory() as root:
        client = FakeClient(
            videos={UP1: [video_item("BVup", "up 基线", 500, author="视频作者名")]},
            acc_errors={UP1},  # acc/info 失败
        )
        targets = [BilibiliTarget(mid=UP1, name="", mode="notice")]
        sender = FakeSender()
        watcher = make_watcher(root, client, sender, targets=targets)
        await watcher._tick()  # 基线（基线轮不解析名字）
        assert client.acc_calls == []

        client.videos[UP1] = [
            video_item("BVnew", "新切片", 600, author="视频作者名"),
            video_item("BVup", "up 基线", 500, author="视频作者名"),
        ]
        await watcher._tick()
        texts = [e[2] for e in sender.events if e[0] == "text" and e[1] == "g1"]
        assert texts == [
            "你关注的视频作者名更新啦：新切片\nhttps://www.bilibili.com/video/BVnew",
        ]
        # author 解析成功同样缓存
        assert watcher.state.target(UP1).name == "视频作者名"


@pytest.mark.asyncio
async def test_name_mid_fallback_not_cached_and_retries():
    with tempfile.TemporaryDirectory() as root:
        client = FakeClient(
            videos={UP2: [video_item("BVa", "基线", 10)]},
            acc_errors={UP2},  # acc/info 失败；无 author 字段
        )
        targets = [BilibiliTarget(mid=UP2, name="", mode="notice")]
        watcher = make_watcher(root, client, targets=targets)
        await watcher._tick()  # 基线 BVa
        client.videos[UP2] = [video_item("BVb", "新稿", 20), video_item("BVa", "基线", 10)]
        sender = FakeSender()
        watcher.sender = sender
        await watcher._tick()
        texts = [e[2] for e in sender.events if e[0] == "text" and e[1] == "g1"]
        assert texts == [f"你关注的{UP2}更新啦：新稿\nhttps://www.bilibili.com/video/BVb"]
        assert watcher.state.target(UP2).name == ""  # 兜底不缓存
        # 下一轮有新视频时重试 acc/info（不永久 stuck）
        client.videos[UP2].insert(0, video_item("BVc", "再新", 30))
        await watcher._tick()
        assert client.acc_calls.count(UP2) == 2


# ---------- watcher：目标/路互不影响、上限、迁移 ----------

@pytest.mark.asyncio
async def test_target_failure_independence():
    with tempfile.TemporaryDirectory() as root:
        client = FakeClient(
            feeds={MID: [feed_item(101)], UP1: [feed_item(55)]},
            videos={UP1: [video_item("BVup", "基线", 500)]},
            feed_errors={MID},  # 官方号图文路 412
        )
        targets = default_targets() + [BilibiliTarget(mid=UP1, name="UP主甲", mode="notice")]
        sender = FakeSender()
        watcher = make_watcher(root, client, sender, targets=targets)
        await watcher._tick()
        # UP1 两路照常建基线，不受 MID 图文路失败影响
        assert watcher.state.target(UP1).last_opus_id == 55
        assert watcher.state.target(UP1).last_bvid == "BVup"
        # 下一轮 UP1 图文照常发通知
        client.feeds[UP1] = [feed_item(56, "新动态"), feed_item(55, "旧")]
        await watcher._tick()
        texts = [e[2] for e in sender.events if e[0] == "text" and e[1] == "g1"]
        assert texts == ["你关注的UP主甲更新啦：新动态\nhttps://www.bilibili.com/opus/56"]


@pytest.mark.asyncio
async def test_notice_opus_max_five_per_round():
    with tempfile.TemporaryDirectory() as root:
        client = FakeClient(feeds={UP1: [feed_item(0, "基线")]}, acc_names={UP1: "某UP"})
        targets = [BilibiliTarget(mid=UP1, name="", mode="notice")]
        watcher = make_watcher(root, client, targets=targets)
        await watcher._tick()

        client.feeds[UP1] = [feed_item(i, f"动态{i}") for i in range(8)]  # 基线 0 + 新 1..7
        sender = FakeSender()
        watcher.sender = sender
        await watcher._tick()
        texts = [e[2] for e in sender.events if e[0] == "text" and e[1] == "g1"]
        assert len(texts) == 5
        assert watcher.state.target(UP1).last_opus_id == 5

        await watcher._tick()
        texts = [e[2] for e in sender.events if e[0] == "text" and e[1] == "g1"]
        assert len(texts) == 7  # 剩余 2 条补发
        assert watcher.state.target(UP1).last_opus_id == 7


@pytest.mark.asyncio
async def test_video_max_five_per_round():
    with tempfile.TemporaryDirectory() as root:
        client = FakeClient(videos={UP1: [video_item("BV0", "基线", 0)]})
        targets = [BilibiliTarget(mid=UP1, name="某UP", mode="notice")]
        watcher = make_watcher(root, client, targets=targets)
        await watcher._tick()

        client.videos[UP1] = [video_item(f"BV{i}", f"视频{i}", 1000 + i) for i in range(1, 8)]
        sender = FakeSender()
        watcher.sender = sender
        await watcher._tick()
        texts = [e[2] for e in sender.events if e[0] == "text" and e[1] == "g1"]
        assert len(texts) == 5
        assert "视频1" in texts[0] and "视频5" in texts[-1]
        assert watcher.state.target(UP1).last_bvid == "BV5"

        await watcher._tick()
        texts = [e[2] for e in sender.events if e[0] == "text" and e[1] == "g1"]
        assert len(texts) == 7


@pytest.mark.asyncio
async def test_migrated_state_does_not_resend():
    with tempfile.TemporaryDirectory() as root:
        path = os.path.join(root, "bilibili_state.json")
        with open(path, "w", encoding="utf-8") as f:
            json.dump({"last_opus_id": 100, "last_video_created": 1000, "last_bvid": "BV0"}, f)
        client = FakeClient(
            feeds={MID: [feed_item(100, "已发"), feed_item(99, "更老")]},
            videos={MID: [video_item("BV0", "已发", 1000), video_item("BVx", "更老", 999)]},
        )
        sender = FakeSender()
        watcher = make_watcher(root, client, sender)  # 默认单目标 full
        await watcher._tick()
        assert sender.events == []
        assert watcher.state.target(MID).last_opus_id == 100


@pytest.mark.asyncio
async def test_restart_does_not_resend_multiple_targets():
    with tempfile.TemporaryDirectory() as root:
        targets = default_targets() + [BilibiliTarget(mid=UP1, name="某UP", mode="notice")]
        client = FakeClient(
            feeds={MID: [feed_item(101)], UP1: [feed_item(55)]},
            videos={MID: [video_item("BV0", "基线", 1000)], UP1: [video_item("BVup", "基线", 500)]},
        )
        watcher = make_watcher(root, client, targets=targets)
        await watcher._tick()

        client2 = FakeClient(
            feeds={MID: [feed_item(101)], UP1: [feed_item(55)]},
            videos={MID: [video_item("BV0", "基线", 1000)], UP1: [video_item("BVup", "基线", 500)]},
        )
        sender2 = FakeSender()
        watcher2 = make_watcher(root, client2, sender2, targets=targets)
        await watcher2._tick()
        assert sender2.events == []


@pytest.mark.asyncio
async def test_no_target_groups_keeps_state_for_retry():
    with tempfile.TemporaryDirectory() as root:
        client = FakeClient(feeds={MID: [feed_item(101)]}, videos={MID: [video_item("BV0", "基线", 1000)]})
        watcher = make_watcher(root, client, groups=[])
        await watcher._tick()
        assert watcher.state.target(MID).last_opus_id == 101

        client.feeds[MID] = [feed_item(102)]
        client.videos[MID] = [video_item("BV1", "新视频", 2000)]
        await watcher._tick()
        assert watcher.state.target(MID).last_opus_id == 101  # 无群不推进
        assert watcher.state.target(MID).last_bvid == "BV0"


@pytest.mark.asyncio
async def test_run_loop_survives_api_error():
    with tempfile.TemporaryDirectory() as root:
        config = make_config(root, bili_kwargs={"interval_seconds": 1})
        watcher = BilibiliWatcher(config, FakeSender())
        watcher._client = FakeClient(feed_errors={MID})

        async def stop_soon():
            await asyncio.sleep(0.05)
            watcher.stop()

        task = asyncio.create_task(stop_soon())
        await watcher.run()
        task.cancel()


@pytest.mark.asyncio
async def test_learned_groups_used_when_no_manual_list():
    with tempfile.TemporaryDirectory() as root:
        config = make_config(root, groups=[])
        config = Config(
            bot=config.bot,
            watch=config.watch,
            target=TargetConfig(group_openids=[], auto_learn_from_events=True),
            upload=config.upload,
            message=config.message,
            bilibili=config.bilibili,
        )
        watcher = BilibiliWatcher(config, FakeClient())
        watcher.group_store.add("learned_g1", "群")
        watcher._client = FakeClient(feeds={MID: [feed_item(101)]})
        await watcher._tick()
        watcher._client.feeds[MID] = [feed_item(102)]
        watcher._client.details = {102: detail_item("102", "1700000200", "公告", [text_para("正文")])}
        sender = FakeSender()
        watcher.sender = sender
        await watcher._tick()
        assert [(e[0], e[1]) for e in sender.events] == [("text", "learned_g1")]


@pytest.mark.asyncio
async def test_disabled_run_returns_immediately():
    with tempfile.TemporaryDirectory() as root:
        config = make_config(root, bili_kwargs={"enabled": False})
        watcher = BilibiliWatcher(config, FakeClient())
        await watcher.run()


# ---------- format ----------

def test_format_notice_text():
    assert format_notice_text("星落官方", "二周年 PV", "https://www.bilibili.com/video/BV2") == \
        "你关注的星落官方更新啦：二周年 PV\nhttps://www.bilibili.com/video/BV2"


def test_format_opus_text_empty_returns_empty():
    assert format_opus_text({"text": "  "}) == ""
    assert format_opus_text({"text": "标题\n正文"}) == "【星落官方动态】标题\n正文"


# ---------- config ----------

def test_config_bilibili_defaults():
    path = write_toml(
        """
[bot]
appid = "123"
secret = "secret"

[watch]
outbox_dir = "/tmp/outbox"
"""
    )
    try:
        cfg = load_config(path)
        assert cfg.bilibili.enabled is True
        assert cfg.bilibili.interval_seconds == 300
        assert cfg.bilibili.sessdata == ""
        # 无 targets：默认单目标 full（官方号）
        assert len(cfg.bilibili.targets) == 1
        t = cfg.bilibili.targets[0]
        assert t.mid == MID and t.name == "星落官方" and t.mode == "full"
    finally:
        os.unlink(path)


def test_config_legacy_mid_honored_without_targets():
    path = write_toml(
        """
[bot]
appid = "123"
secret = "secret"

[watch]
outbox_dir = "/tmp/outbox"

[bilibili]
mid = 12345
"""
    )
    try:
        cfg = load_config(path)
        assert len(cfg.bilibili.targets) == 1
        assert cfg.bilibili.targets[0].mid == 12345
        assert cfg.bilibili.targets[0].mode == "full"
    finally:
        os.unlink(path)


def test_config_multi_targets_parsing():
    path = write_toml(
        """
[bot]
appid = "123"
secret = "secret"

[watch]
outbox_dir = "/tmp/outbox"

[bilibili]
enabled = true
interval_seconds = 60
sessdata = "SESS_VALUE"

[[bilibili.targets]]
mid = "3537105431038140"
name = "星落官方"
mode = "full"

[[bilibili.targets]]
mid = "11310554"
name = ""
mode = "notice"

[[bilibili.targets]]
mid = "1623229430"
name = ""
mode = "notice"
"""
    )
    try:
        cfg = load_config(path)
        assert cfg.bilibili.interval_seconds == 60
        assert cfg.bilibili.sessdata == "SESS_VALUE"
        assert len(cfg.bilibili.targets) == 3
        official, up1, up2 = cfg.bilibili.targets
        assert (official.mid, official.name, official.mode) == (MID, "星落官方", "full")
        assert (up1.mid, up1.name, up1.mode) == (UP1, "", "notice")
        assert (up2.mid, up2.name, up2.mode) == (UP2, "", "notice")
    finally:
        os.unlink(path)


def test_config_target_mode_defaults_full_and_validation():
    path = write_toml(
        """
[bot]
appid = "123"
secret = "secret"

[watch]
outbox_dir = "/tmp/outbox"

[[bilibili.targets]]
mid = "11310554"
"""
    )
    try:
        cfg = load_config(path)
        assert cfg.bilibili.targets[0].mode == "full"  # mode 缺省默认 full
    finally:
        os.unlink(path)

    bad_mode = write_toml(
        """
[bot]
appid = "123"
secret = "secret"

[watch]
outbox_dir = "/tmp/outbox"

[[bilibili.targets]]
mid = "11310554"
mode = "verbose"
"""
    )
    try:
        with pytest.raises(ValueError, match="mode"):
            load_config(bad_mode)
    finally:
        os.unlink(bad_mode)


def test_config_target_mid_required_and_unique():
    missing_mid = write_toml(
        """
[bot]
appid = "123"
secret = "secret"

[watch]
outbox_dir = "/tmp/outbox"

[[bilibili.targets]]
name = "无名"
"""
    )
    try:
        with pytest.raises(ValueError, match="mid"):
            load_config(missing_mid)
    finally:
        os.unlink(missing_mid)

    dup = write_toml(
        """
[bot]
appid = "123"
secret = "secret"

[watch]
outbox_dir = "/tmp/outbox"

[[bilibili.targets]]
mid = "11310554"

[[bilibili.targets]]
mid = "11310554"
"""
    )
    try:
        with pytest.raises(ValueError, match="重复"):
            load_config(dup)
    finally:
        os.unlink(dup)


def test_config_bilibili_bad_interval():
    path = write_toml(
        """
[bot]
appid = "123"
secret = "secret"

[watch]
outbox_dir = "/tmp/outbox"

[bilibili]
interval_seconds = 0
"""
    )
    try:
        with pytest.raises(ValueError, match="interval"):
            load_config(path)
    finally:
        os.unlink(path)
