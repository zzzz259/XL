"""QueryHandler 立绘查询：末尾数字剥离、候选保留序号、与图鉴/纯数字选择协议共存。"""

import hashlib
import json
from pathlib import Path

import pytest

from bot_app.matcher import CharacterMatcher
from bot_app.portrait_querier import PortraitQuerier
from bot_app.query_handler import QueryHandler, _split_portrait_ordinal
from bot_app.querier import CharacterQuerier
from bot_app.selection import SelectionStore
from bot_app.updater import ServiceMute

CHARACTERS = {
    "10000101": {"name": "菲尼斯/Finis", "star": 5},
    "10000224": {"name": "雾铃/Bell", "star": 4},
    "10000333": {"name": "迷迭香/Rosemary", "star": 6},
    "10000444": {"name": "白泽/BaiZe", "star": 5},
}

# 最小合法 PNG（8 字节签名 + IHDR 宽2高3 + IEND）
PNG_2X3 = (
    b"\x89PNG\r\n\x1a\n"
    + b"\x00\x00\x00\x0dIHDR"
    + b"\x00\x00\x00\x02\x00\x00\x00\x03\x08\x06\x00\x00\x00"
    + b"\x00\x00\x00\x00IEND\xaeB`\x82"
)
PNG_SHA = hashlib.sha256(PNG_2X3).hexdigest()

# 菲尼斯 2 套（1 published / 2 not_generated）；雾铃/迷迭香各 1 套 published；白泽无立绘记录
SKINS = [
    {"character_id": "10000101", "skin_id": "1000010101", "ordinal": 1,
     "name": "菲尼斯", "state": "published"},
    {"character_id": "10000101", "skin_id": "1000010102", "ordinal": 2,
     "name": "菲尼斯·觉醒", "state": "not_generated"},
    {"character_id": "10000224", "skin_id": "1000022401", "ordinal": 1,
     "name": "雾铃", "state": "published"},
    {"character_id": "10000333", "skin_id": "1000033301", "ordinal": 1,
     "name": "迷迭香", "state": "published"},
]


class FakeSender:
    def __init__(self):
        self.calls = []

    async def send_text(self, group, text, reply_to=""):
        self.calls.append(("text", group, text))
        return True

    async def send_image(self, file_path, content, groups, reply_to=""):
        self.calls.append(("image", list(groups), file_path))
        return {g: True for g in groups}

    async def send_schedule_image(self, file_path, content, groups, reply_to=""):
        self.calls.append(("schedule_image", list(groups), file_path, content))
        return {g: True for g in groups}


class FakeSenderNoSchedule:
    """旧 sender：没有强制 multipart 的立绘入口（走 getattr 兼容降级）。"""

    def __init__(self):
        self.calls = []

    async def send_text(self, group, text, reply_to=""):
        self.calls.append(("text", group, text))
        return True

    async def send_image(self, file_path, content, groups, reply_to=""):
        self.calls.append(("image", list(groups), file_path))
        return {g: True for g in groups}


def make_handler(tmp_path, aliases=None, mute=None, sender=None):
    data_dir = tmp_path / "data"
    char_file = data_dir / "character_data" / "current.json"
    char_file.parent.mkdir(parents=True, exist_ok=True)
    char_file.write_text(
        json.dumps({"characters": CHARACTERS}, ensure_ascii=False), encoding="utf-8"
    )
    if aliases:
        (data_dir / "aliases.json").write_text(
            json.dumps(aliases, ensure_ascii=False), encoding="utf-8"
        )
    versions_dir = data_dir / "versions"
    cards = versions_dir / "123" / "character_cards"
    cards.mkdir(parents=True)
    for cid in CHARACTERS:
        (cards / f"{cid}_角色档案_长图.png").write_bytes(b"png")
    (data_dir / "current_version.json").write_text('{"version": 123}', encoding="utf-8")

    # 立绘版本产物
    portraits = versions_dir / "123" / "portraits"
    manifest_skins = []
    for skin in SKINS:
        item = {
            "character_id": skin["character_id"],
            "skin_id": skin["skin_id"],
            "ordinal": skin["ordinal"],
            "name": skin["name"],
            "input_fingerprint": f"fp-{skin['skin_id']}",
            "state": skin["state"],
            "artifact_path": None,
            "artifact_sha256": None,
            "source_version": 123 if skin["state"] == "published" else None,
            "width": None,
            "height": None,
            "error": None,
        }
        if skin["state"] == "published":
            item["artifact_path"] = f"{skin['character_id']}/{skin['skin_id']}.png"
            item["artifact_sha256"] = PNG_SHA
            item["width"] = 2
            item["height"] = 3
            sub = portraits / skin["character_id"]
            sub.mkdir(parents=True, exist_ok=True)
            (sub / f"{skin['skin_id']}.png").write_bytes(PNG_2X3)
        manifest_skins.append(item)
    portraits.mkdir(parents=True, exist_ok=True)
    (portraits / "manifest.json").write_text(
        json.dumps({"schema_version": 1, "version": "123", "skins": manifest_skins}),
        encoding="utf-8",
    )

    sender = sender or FakeSender()
    handler = QueryHandler(
        CharacterQuerier(char_file, versions_dir),
        CharacterMatcher(char_file, data_dir),
        SelectionStore(),
        sender,
        bot_openid="",
        mute=mute,
        portrait_querier=PortraitQuerier(char_file, versions_dir),
    )
    return handler, sender, data_dir


# ---------- 序号剥离 ----------

def test_split_portrait_ordinal():
    assert _split_portrait_ordinal("雾铃1") == ("雾铃", 1)
    assert _split_portrait_ordinal("雾铃 2") == ("雾铃", 2)
    assert _split_portrait_ordinal("菲尼斯 10") == ("菲尼斯", 10)
    assert _split_portrait_ordinal("菲尼斯　3") == ("菲尼斯", 3)  # 全角空格


def test_split_portrait_ordinal_no_digit():
    assert _split_portrait_ordinal("雾铃") == ("雾铃", None)
    assert _split_portrait_ordinal("1号") == ("1号", None)
    assert _split_portrait_ordinal("") == ("", None)
    assert _split_portrait_ordinal("12") == ("12", None)  # 纯数字走候选选择协议
    assert _split_portrait_ordinal("10000101") == ("10000101", None)  # 纯数字 ID 不剥离
    assert _split_portrait_ordinal("菲尼斯觉醒2世") == ("菲尼斯觉醒2世", None)


# ---------- 数字立绘查询 ----------

@pytest.mark.asyncio
async def test_query_without_digit_keeps_card_behavior(tmp_path):
    handler, sender, _ = make_handler(tmp_path)
    await handler._answer_query("g1", "雾铃", "u1")
    assert [c[0] for c in sender.calls] == ["image"]
    assert "10000224" in sender.calls[0][2]


@pytest.mark.asyncio
async def test_query_with_digit_sends_portrait(tmp_path):
    handler, sender, _ = make_handler(tmp_path)
    await handler._answer_query("g1", "雾铃1", "u1")
    assert [c[0] for c in sender.calls] == ["schedule_image"]
    _, groups, path, content = sender.calls[0]
    assert groups == ["g1"]
    assert Path(path).parent.name == "10000224"
    assert Path(path).name == "1000022401.png"
    assert content == ""  # 当前版本立绘不配旧版提示


@pytest.mark.asyncio
async def test_query_digit_with_space_and_fullwidth(tmp_path):
    handler, sender, _ = make_handler(tmp_path)
    await handler._answer_query("g1", "雾铃 1", "u1")
    await handler._answer_query("g1", "菲尼斯　1", "u1")
    assert [c[0] for c in sender.calls] == ["schedule_image", "schedule_image"]


@pytest.mark.asyncio
async def test_query_digit_by_alias(tmp_path):
    handler, sender, _ = make_handler(tmp_path, aliases={"雾铃": ["铃铛"]})
    await handler._answer_query("g1", "铃铛1", "u1")
    assert [c[0] for c in sender.calls] == ["schedule_image"]
    assert Path(sender.calls[0][2]).name == "1000022401.png"


@pytest.mark.asyncio
async def test_query_digit_by_pinyin(tmp_path):
    handler, sender, _ = make_handler(tmp_path)
    await handler._answer_query("g1", "wuling 1", "u1")
    assert [c[0] for c in sender.calls] == ["schedule_image"]


@pytest.mark.asyncio
async def test_query_digit_guess_notice_then_portrait(tmp_path):
    handler, sender, _ = make_handler(tmp_path)
    await handler._answer_query("g1", "feinis1", "u1")  # 拼音相似 → guess
    assert [c[0] for c in sender.calls] == ["text", "schedule_image"]
    assert sender.calls[0][2] == "已为你匹配到：菲尼斯"


@pytest.mark.asyncio
async def test_query_digit_not_found(tmp_path):
    handler, sender, _ = make_handler(tmp_path)
    await handler._answer_query("g1", "不存在的角色9", "u1")
    assert sender.calls == [("text", "g1", "未找到角色：不存在的角色9")]


# ---------- 未生成 / 失败 / 越界 / 静音 ----------

@pytest.mark.asyncio
async def test_query_digit_not_generated_only_notice(tmp_path):
    handler, sender, _ = make_handler(tmp_path)
    await handler._answer_query("g1", "菲尼斯2", "u1")
    assert [c[0] for c in sender.calls] == ["text"]
    text = sender.calls[0][2]
    assert "菲尼斯" in text and "未生成" in text


@pytest.mark.asyncio
async def test_query_digit_out_of_range_shows_available(tmp_path):
    handler, sender, _ = make_handler(tmp_path)
    await handler._answer_query("g1", "菲尼斯5", "u1")
    assert [c[0] for c in sender.calls] == ["text"]
    assert "1-2" in sender.calls[0][2]


@pytest.mark.asyncio
async def test_query_digit_no_portrait_record(tmp_path):
    handler, sender, _ = make_handler(tmp_path)
    await handler._answer_query("g1", "白泽1", "u1")
    assert [c[0] for c in sender.calls] == ["text"]
    assert "暂无立绘" in sender.calls[0][2]


@pytest.mark.asyncio
async def test_query_digit_muted(tmp_path):
    mute = ServiceMute()
    handler, sender, _ = make_handler(tmp_path, mute=mute)
    mute.muted = True
    await handler._answer_query("g1", "雾铃1", "u1")
    assert sender.calls == []


@pytest.mark.asyncio
async def test_query_digit_without_schedule_sender_falls_back(tmp_path):
    handler, sender, _ = make_handler(tmp_path, sender=FakeSenderNoSchedule())
    await handler._answer_query("g1", "雾铃1", "u1")
    assert [c[0] for c in sender.calls] == ["text"]
    assert "暂不可用" in sender.calls[0][2]


# ---------- 候选保留立绘序号 ----------

@pytest.mark.asyncio
async def test_candidates_keep_portrait_ordinal_and_select(tmp_path):
    handler, sender, _ = make_handler(tmp_path)
    await handler._answer_query("g1", "迷迭1", "u1")
    assert [c[0] for c in sender.calls] == ["text"]
    pending = handler._selection.get("g1", "u1")
    assert pending is not None
    assert pending.intent == "portrait"
    assert pending.portrait_ordinal == 1

    handled = await handler._handle_selection("g1", "u1", "1", "m2")
    assert handled
    assert [c[0] for c in sender.calls] == ["text", "schedule_image"]
    assert Path(sender.calls[-1][2]).name == "1000033301.png"


@pytest.mark.asyncio
async def test_candidates_card_intent_selects_card(tmp_path):
    """无末尾数字的模糊查询：候选选择仍走原图鉴（协议不回归）。"""
    handler, sender, _ = make_handler(tmp_path)
    await handler._answer_query("g1", "菲尼", "u1")
    pending = handler._selection.get("g1", "u1")
    assert pending is not None
    assert pending.intent == "card"
    assert pending.portrait_ordinal is None

    handled = await handler._handle_selection("g1", "u1", "1", "m2")
    assert handled
    assert [c[0] for c in sender.calls] == ["text", "image"]


@pytest.mark.asyncio
async def test_pure_digit_query_still_selection_protocol(tmp_path):
    """"1" 这种纯数字消息只消费候选选择，不会被当立绘查询。"""
    handler, sender, _ = make_handler(tmp_path)
    handled = await handler._handle_selection("g1", "u1", "1")
    assert not handled
    assert sender.calls == []


@pytest.mark.asyncio
async def test_digit_choice_cancels_portrait_pending(tmp_path):
    handler, sender, _ = make_handler(tmp_path)
    await handler._answer_query("g1", "菲尼2", "u1")
    handled = await handler._handle_selection("g1", "u1", "2")  # 都不是
    assert handled
    assert sender.calls[-1] == ("text", "g1", "好的，已取消本次选择")
    assert handler._selection.get("g1", "u1") is None


@pytest.mark.asyncio
async def test_invalid_digit_keeps_portrait_pending(tmp_path):
    handler, sender, _ = make_handler(tmp_path)
    await handler._answer_query("g1", "菲尼2", "u1")
    handled = await handler._handle_selection("g1", "u1", "9")
    assert handled
    assert "请输入 1-2 之间的数字" in sender.calls[-1][2]
    assert handler._selection.get("g1", "u1") is not None
