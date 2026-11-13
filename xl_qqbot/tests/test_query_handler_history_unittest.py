"""QueryHandler 内容历史：统一右端语法 `角色名 [立绘序号] [- [历史序号]]`、历史意图候选保留、
单历史与全部版本逐张纯图片发送（content=""）、失败停止，以及当前普通查询兼容。

历史序号语义：-1/-2 为前 1/2 个不同内容（不含当前），末尾单独 - 为含当前的全部版本（新到旧）。
-0、残缺后缀不静默改意图，走正常查询错误（未找到角色：原词）。
"""

import hashlib
import json
from pathlib import Path

import pytest

from bot_app import query_handler as query_handler_module
from bot_app.matcher import CharacterMatcher
from bot_app.portrait_querier import PortraitQuerier
from bot_app.querier import CharacterQuerier
from bot_app.query_handler import QueryHandler
from bot_app.selection import SelectionStore
from bot_app.updater import ServiceMute

CID_W = "10000224"  # 雾铃：图鉴 3 个内容修订、立绘 3 个内容修订
CID_F = "10000101"  # 菲尼斯：图鉴 2 个内容修订（拼音 guess 用）
CID_M = "10000333"  # 迷迭香：图鉴/立绘各 2 个内容修订（模糊候选用）
SKIN_W = "1000022401"
SKIN_M = "1000033301"

CHARACTERS = {
    CID_F: {"name": "菲尼斯/Finis", "star": 5},
    CID_W: {"name": "雾铃/Bell", "star": 4},
    CID_M: {"name": "迷迭香/Rosemary", "star": 6},
}

# 最小合法 PNG（8 字节签名 + IHDR + IEND）


def make_png(width: int, height: int) -> bytes:
    return (
        b"\x89PNG\r\n\x1a\n"
        + b"\x00\x00\x00\x0dIHDR"
        + width.to_bytes(4, "big")
        + height.to_bytes(4, "big")
        + b"\x08\x06\x00\x00\x00"
        + b"\x00\x00\x00\x00IEND\xaeB`\x82"
    )


PNG = {
    "A": make_png(2, 3),
    "B": make_png(4, 5),
    "C": make_png(6, 7),
    "F2": make_png(8, 3),
    "F": make_png(10, 3),
    "M2": make_png(12, 3),
    "M3": make_png(14, 3),
    "P1": make_png(2, 5),
    "P2": make_png(4, 7),
    "P3": make_png(6, 9),
    "PM1": make_png(3, 4),
    "PM2": make_png(5, 6),
    "PM3": make_png(7, 8),
}


def _sha(data: str) -> str:
    return hashlib.sha256(data.encode("utf-8")).hexdigest()


def write_card_version(versions_dir, version, entries):
    """entries: list of dict(character_id, key) — key 指向 PNG 字典。"""
    cards_dir = versions_dir / str(version) / "character_cards"
    cards_dir.mkdir(parents=True, exist_ok=True)
    manifest = []
    for entry in entries:
        png = PNG[entry["key"]]
        filename = f"{entry['character_id']}_角色档案_长图.png"
        (cards_dir / filename).write_bytes(png)
        manifest.append(
            {
                "character_id": entry["character_id"],
                "state": "published",
                "artifact_path": f"character_cards/{filename}",
                "artifact_sha256": hashlib.sha256(png).hexdigest(),
                "data_sha256": _sha(f"data-{entry['character_id']}-{entry['key']}"),
                "source_version": str(version),
            }
        )
    (cards_dir / "manifest.json").write_text(
        json.dumps(
            {"schema_version": 1, "version": str(version), "characters": manifest},
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )


def write_portrait_version(versions_dir, version, skins):
    """skins: list of dict(character_id, skin_id, ordinal, key)。"""
    pdir = versions_dir / str(version) / "portraits"
    sub = None
    items = []
    for skin in skins:
        png = PNG[skin["key"]]
        sub = pdir / skin["character_id"]
        sub.mkdir(parents=True, exist_ok=True)
        (sub / f"{skin['skin_id']}.png").write_bytes(png)
        items.append(
            {
                "character_id": skin["character_id"],
                "skin_id": skin["skin_id"],
                "ordinal": skin["ordinal"],
                "name": f"皮肤{skin['skin_id'][-2:]}",
                "input_fingerprint": f"fp-{skin['skin_id']}",
                "state": "published",
                "artifact_path": f"{skin['character_id']}/{skin['skin_id']}.png",
                "artifact_sha256": hashlib.sha256(png).hexdigest(),
                "source_version": str(version),
                "width": int.from_bytes(png[16:20], "big"),
                "height": int.from_bytes(png[20:24], "big"),
                "error": None,
                "render_scope": "full",
                "render_notice": None,
            }
        )
    pdir.mkdir(parents=True, exist_ok=True)
    (pdir / "manifest.json").write_text(
        json.dumps(
            {"schema_version": 1, "version": str(version), "skins": items},
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )


class FakeSender:
    """记录 content/reply_to；fail_paths 中的文件走本地 multipart 也返回失败。"""

    def __init__(self):
        self.calls = []
        self.fail_paths = set()

    async def send_text(self, group, text, reply_to=""):
        self.calls.append(("text", group, text, reply_to))
        return True

    async def send_image(self, file_path, content, groups, reply_to=""):
        self.calls.append(("image", file_path, content, list(groups), reply_to))
        return True

    async def send_schedule_image(self, file_path, content, groups, reply_to=""):
        self.calls.append(("schedule_image", file_path, content, list(groups), reply_to))
        return {g: str(file_path) not in self.fail_paths for g in groups}


class FakeSenderNoSchedule(FakeSender):
    """旧 sender：没有强制 multipart 的本地图片入口。"""

    send_schedule_image = None


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
    # 图鉴：雾铃 A→B→C；菲尼斯仅 122/123；迷迭香 M2→M3
    write_card_version(versions_dir, 121, [{"character_id": CID_W, "key": "A"}])
    write_card_version(
        versions_dir,
        122,
        [
            {"character_id": CID_W, "key": "B"},
            {"character_id": CID_F, "key": "F2"},
            {"character_id": CID_M, "key": "M2"},
        ],
    )
    write_card_version(
        versions_dir,
        123,
        [
            {"character_id": CID_W, "key": "C"},
            {"character_id": CID_F, "key": "F"},
            {"character_id": CID_M, "key": "M3"},
        ],
    )
    # 立绘：雾铃 P1→P2→P3；迷迭香 PM1→PM2→PM3
    write_portrait_version(
        versions_dir,
        121,
        [
            {"character_id": CID_W, "skin_id": SKIN_W, "ordinal": 1, "key": "P1"},
            {"character_id": CID_M, "skin_id": SKIN_M, "ordinal": 1, "key": "PM1"},
        ],
    )
    write_portrait_version(
        versions_dir,
        122,
        [
            {"character_id": CID_W, "skin_id": SKIN_W, "ordinal": 1, "key": "P2"},
            {"character_id": CID_M, "skin_id": SKIN_M, "ordinal": 1, "key": "PM2"},
        ],
    )
    write_portrait_version(
        versions_dir,
        123,
        [
            {"character_id": CID_W, "skin_id": SKIN_W, "ordinal": 1, "key": "P3"},
            {"character_id": CID_M, "skin_id": SKIN_M, "ordinal": 1, "key": "PM3"},
        ],
    )
    (data_dir / "current_version.json").write_text('{"version": 123}', encoding="utf-8")

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


def card_path(data_dir, cid, version):
    return (
        data_dir / "versions" / str(version) / "character_cards" / f"{cid}_角色档案_长图.png"
    )


def portrait_path(data_dir, cid, skin_id, version):
    return data_dir / "versions" / str(version) / "portraits" / cid / f"{skin_id}.png"


def sent_paths(sender):
    return [Path(call[1]) for call in sender.calls if call[0] in ("image", "schedule_image")]


# ---------- 统一右端语法解析（八种语法 + 拒绝） ----------


def _spec_tuple(spec):
    return (
        spec.name,
        spec.portrait_ordinal,
        spec.history_offset,
        spec.history_all,
        spec.valid,
    )


def test_parse_spec_card_history_syntaxes():
    parse = query_handler_module._parse_query_spec
    assert _spec_tuple(parse("雾铃")) == ("雾铃", None, None, False, True)
    assert _spec_tuple(parse("雾铃-")) == ("雾铃", None, None, True, True)
    assert _spec_tuple(parse("雾铃-1")) == ("雾铃", None, 1, False, True)
    assert _spec_tuple(parse("雾铃-2")) == ("雾铃", None, 2, False, True)


def test_parse_spec_portrait_history_syntaxes():
    parse = query_handler_module._parse_query_spec
    assert _spec_tuple(parse("雾铃1")) == ("雾铃", 1, None, False, True)
    assert _spec_tuple(parse("雾铃1-")) == ("雾铃", 1, None, True, True)
    assert _spec_tuple(parse("雾铃1-1")) == ("雾铃", 1, 1, False, True)
    assert _spec_tuple(parse("雾铃1-2")) == ("雾铃", 1, 2, False, True)


def test_parse_spec_rejects_zero_history_suffix():
    parse = query_handler_module._parse_query_spec
    # -0 不静默改意图：标为非法，handler 直接给正常查询错误（不经过模糊匹配）
    assert _spec_tuple(parse("雾铃-0")) == ("雾铃-0", None, None, False, False)
    assert _spec_tuple(parse("雾铃1-0")) == ("雾铃1-0", None, None, False, False)


def test_parse_spec_keeps_pure_digit_and_inner_digit_names():
    parse = query_handler_module._parse_query_spec
    assert _spec_tuple(parse("12")) == ("12", None, None, False, True)  # 纯数字走候选选择协议
    assert _spec_tuple(parse("10000101")) == ("10000101", None, None, False, True)
    assert _spec_tuple(parse("菲尼斯觉醒2世")) == ("菲尼斯觉醒2世", None, None, False, True)


# ---------- 图鉴历史：单历史与全部版本只发图片 ----------


@pytest.mark.asyncio
async def test_card_history_single_sends_only_image_empty_content(tmp_path):
    handler, sender, data_dir = make_handler(tmp_path)
    await handler._answer_query("g1", "雾铃-1", "u1", "m1")
    assert len(sender.calls) == 1  # 角色-1 是图鉴历史，不是立绘 1
    kind, path, content, groups, reply_to = sender.calls[0]
    assert kind == "schedule_image"  # 历史图片一律本地 multipart
    assert Path(path) == card_path(data_dir, CID_W, 122)
    assert content == ""  # 不配版本说明/历史序号/匹配说明
    assert groups == ["g1"]
    assert reply_to == "m1"


@pytest.mark.asyncio
async def test_card_history_second_offset_older_image(tmp_path):
    handler, sender, data_dir = make_handler(tmp_path)
    await handler._answer_query("g1", "雾铃-2", "u1", "m1")
    assert len(sender.calls) == 1
    assert sender.calls[0][0] == "schedule_image"
    assert Path(sender.calls[0][1]) == card_path(data_dir, CID_W, 121)
    assert sender.calls[0][2] == ""


@pytest.mark.asyncio
async def test_card_all_versions_sends_newest_first_empty_content_no_text(tmp_path):
    handler, sender, data_dir = make_handler(tmp_path)
    await handler._answer_query("g1", "雾铃-", "u1", "m1")
    assert [call[0] for call in sender.calls] == ["schedule_image"] * 3
    assert sent_paths(sender) == [
        card_path(data_dir, CID_W, 123),
        card_path(data_dir, CID_W, 122),
        card_path(data_dir, CID_W, 121),
    ]
    assert all(call[2] == "" for call in sender.calls)  # 每张均空文字
    assert all(call[4] == "m1" for call in sender.calls)  # 保留被动回复
    # 成功时不发版本目录、说明或汇总文字


# ---------- 立绘历史 ----------


@pytest.mark.asyncio
async def test_portrait_history_single_empty_content(tmp_path):
    handler, sender, data_dir = make_handler(tmp_path)
    await handler._answer_query("g1", "雾铃1-1", "u1", "m1")
    assert len(sender.calls) == 1
    kind, path, content, _groups, _reply_to = sender.calls[0]
    assert kind == "schedule_image"
    assert Path(path) == portrait_path(data_dir, CID_W, SKIN_W, 122)
    assert content == ""


@pytest.mark.asyncio
async def test_portrait_history_all_versions_order_and_empty_content(tmp_path):
    handler, sender, data_dir = make_handler(tmp_path)
    await handler._answer_query("g1", "雾铃1-", "u1", "m1")
    assert [call[0] for call in sender.calls] == ["schedule_image"] * 3
    assert sent_paths(sender) == [
        portrait_path(data_dir, CID_W, SKIN_W, 123),
        portrait_path(data_dir, CID_W, SKIN_W, 122),
        portrait_path(data_dir, CID_W, SKIN_W, 121),
    ]
    assert all(call[2] == "" for call in sender.calls)


# ---------- 错误：越界 / -0 / 残缺后缀 ----------


@pytest.mark.asyncio
async def test_card_history_out_of_range_shows_total(tmp_path):
    handler, sender, _ = make_handler(tmp_path)
    await handler._answer_query("g1", "菲尼斯-2", "u1", "m1")  # 菲尼斯只有 2 个修订
    assert len(sender.calls) == 1
    kind, _, text, _ = sender.calls[0]
    assert kind == "text"
    assert "菲尼斯" in text and "2 个" in text
    assert sent_paths(sender) == []


@pytest.mark.asyncio
async def test_history_zero_suffix_is_normal_query_error(tmp_path):
    handler, sender, _ = make_handler(tmp_path)
    await handler._answer_query("g1", "雾铃-0", "u1", "m1")
    assert sender.calls == [("text", "g1", "未找到角色：雾铃-0", "m1")]
    assert sent_paths(sender) == []


@pytest.mark.asyncio
async def test_portrait_history_zero_suffix_is_normal_query_error(tmp_path):
    handler, sender, _ = make_handler(tmp_path)
    await handler._answer_query("g1", "雾铃1-0", "u1", "m1")
    assert sender.calls == [("text", "g1", "未找到角色：雾铃1-0", "m1")]
    assert sent_paths(sender) == []


@pytest.mark.asyncio
async def test_malformed_suffix_is_normal_query_error(tmp_path):
    handler, sender, _ = make_handler(tmp_path)
    await handler._answer_query("g1", "雾铃-abc", "u1", "m1")
    assert sender.calls == [("text", "g1", "未找到角色：雾铃-abc", "m1")]
    assert sent_paths(sender) == []


# ---------- 别名 / guess 兼容历史意图 ----------


@pytest.mark.asyncio
async def test_history_query_by_alias(tmp_path):
    handler, sender, data_dir = make_handler(tmp_path, aliases={"雾铃": ["铃铛"]})
    await handler._answer_query("g1", "铃铛-1", "u1", "m1")
    assert len(sender.calls) == 1
    assert sender.calls[0][0] == "schedule_image"
    assert Path(sender.calls[0][1]) == card_path(data_dir, CID_W, 122)


@pytest.mark.asyncio
async def test_history_query_guess_notice_then_image(tmp_path):
    handler, sender, data_dir = make_handler(tmp_path)
    await handler._answer_query("g1", "feinis-1", "u1", "m1")  # 拼音相似 → guess
    assert [call[0] for call in sender.calls] == ["text", "schedule_image"]
    assert sender.calls[0][2] == "已为你匹配到：菲尼斯"
    assert Path(sender.calls[1][1]) == card_path(data_dir, CID_F, 122)
    assert sender.calls[1][2] == ""


# ---------- 候选保留完整历史意图 ----------


@pytest.mark.asyncio
async def test_fuzzy_candidates_keep_card_history_intent(tmp_path):
    handler, sender, data_dir = make_handler(tmp_path)
    await handler._answer_query("g1", "迷迭-1", "u1", "m1")
    assert sender.calls[0][0] == "text"  # 候选列表
    pending = handler._selection.get("g1", "u1")
    assert pending is not None
    assert pending.intent == "card"
    assert pending.history_offset == 1
    assert pending.history_all is False
    assert pending.portrait_ordinal is None
    assert pending.query == "迷迭"  # 学习输入只含名字部分

    handled = await handler._handle_selection("g1", "u1", "1", "m2")
    assert handled
    assert sender.calls[-1][0] == "schedule_image"
    assert Path(sender.calls[-1][1]) == card_path(data_dir, CID_M, 122)
    assert sender.calls[-1][2] == ""


@pytest.mark.asyncio
async def test_fuzzy_candidates_keep_portrait_all_intent(tmp_path):
    handler, sender, data_dir = make_handler(tmp_path)
    await handler._answer_query("g1", "迷迭1-", "u1", "m1")
    pending = handler._selection.get("g1", "u1")
    assert pending is not None
    assert pending.intent == "portrait"
    assert pending.portrait_ordinal == 1
    assert pending.history_all is True
    assert pending.history_offset is None
    assert pending.query == "迷迭"

    handled = await handler._handle_selection("g1", "u1", "1", "m2")
    assert handled
    assert [call[0] for call in sender.calls[-3:]] == ["schedule_image"] * 3
    assert sent_paths(sender) == [
        portrait_path(data_dir, CID_M, SKIN_M, 123),
        portrait_path(data_dir, CID_M, SKIN_M, 122),
        portrait_path(data_dir, CID_M, SKIN_M, 121),
    ]


@pytest.mark.asyncio
async def test_invalid_choice_keeps_history_pending(tmp_path):
    handler, sender, _ = make_handler(tmp_path)
    await handler._answer_query("g1", "迷迭-1", "u1", "m1")
    handled = await handler._handle_selection("g1", "u1", "9", "m2")
    assert handled
    assert "请输入 1-2 之间的数字" in sender.calls[-1][2]
    pending = handler._selection.get("g1", "u1")
    assert pending is not None  # 保留待选择及历史意图
    assert pending.history_offset == 1


# ---------- 失败停止 / 数据不可用 ----------


@pytest.mark.asyncio
async def test_all_versions_upload_failure_stops_with_single_error(tmp_path):
    handler, sender, data_dir = make_handler(tmp_path)
    sender.fail_paths.add(str(card_path(data_dir, CID_W, 122)))
    await handler._answer_query("g1", "雾铃-", "u1", "m1")
    assert [call[0] for call in sender.calls] == [
        "schedule_image",
        "schedule_image",
        "text",
    ]
    assert sent_paths(sender) == [
        card_path(data_dir, CID_W, 123),
        card_path(data_dir, CID_W, 122),  # 第二张失败
    ]
    assert "失败" in sender.calls[-1][2]  # 一条错误提示，不伪报全部成功


@pytest.mark.asyncio
async def test_all_versions_invalid_revision_stops_with_error(tmp_path):
    handler, sender, data_dir = make_handler(tmp_path)
    manifest_path = (
        data_dir / "versions" / "122" / "character_cards" / "manifest.json"
    )
    payload = json.loads(manifest_path.read_text(encoding="utf-8"))
    for entry in payload["characters"]:
        if entry["character_id"] == CID_W:
            entry["artifact_sha256"] = "0" * 64  # 中间修订坏 SHA
    manifest_path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")

    await handler._answer_query("g1", "雾铃-", "u1", "m1")
    assert [call[0] for call in sender.calls] == ["schedule_image", "text"]
    assert sent_paths(sender) == [card_path(data_dir, CID_W, 123)]
    assert "不可用" in sender.calls[-1][2]


@pytest.mark.asyncio
async def test_single_history_unavailable_notices_only(tmp_path):
    handler, sender, data_dir = make_handler(tmp_path)
    manifest_path = (
        data_dir / "versions" / "122" / "character_cards" / "manifest.json"
    )
    payload = json.loads(manifest_path.read_text(encoding="utf-8"))
    for entry in payload["characters"]:
        if entry["character_id"] == CID_W:
            entry["artifact_sha256"] = "0" * 64
    manifest_path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")

    await handler._answer_query("g1", "雾铃-1", "u1", "m1")
    assert len(sender.calls) == 1
    assert sender.calls[0][0] == "text"
    assert "不可用" in sender.calls[0][2]
    assert sent_paths(sender) == []


@pytest.mark.asyncio
async def test_all_versions_without_schedule_sender_falls_back(tmp_path):
    handler, sender, _ = make_handler(tmp_path, sender=FakeSenderNoSchedule())
    await handler._answer_query("g1", "雾铃-", "u1", "m1")
    assert len(sender.calls) == 1
    assert sender.calls[0][0] == "text"
    assert "不可用" in sender.calls[0][2]
    assert sent_paths(sender) == []


@pytest.mark.asyncio
async def test_portrait_all_invalid_middle_manifest_stops(tmp_path):
    """中间版本 portraits manifest 损坏：按失败停止规则处理，不当作间断漏发后继续。"""
    handler, sender, data_dir = make_handler(tmp_path)
    manifest = data_dir / "versions" / "122" / "portraits" / "manifest.json"
    manifest.write_text("{broken", encoding="utf-8")
    await handler._answer_query("g1", "雾铃1-", "u1", "m1")
    assert [call[0] for call in sender.calls] == ["schedule_image", "text"]
    assert sent_paths(sender) == [portrait_path(data_dir, CID_W, SKIN_W, 123)]
    assert "不可用" in sender.calls[-1][2]


# ---------- 回归：静音 / 复刻 / 当前普通查询 ----------


@pytest.mark.asyncio
async def test_history_query_muted(tmp_path):
    mute = ServiceMute()
    handler, sender, _ = make_handler(tmp_path, mute=mute)
    mute.muted = True
    await handler._answer_query("g1", "雾铃-", "u1", "m1")
    assert sender.calls == []


@pytest.mark.asyncio
async def test_rerun_query_priority_unchanged(tmp_path):
    handler, sender, _ = make_handler(tmp_path)
    await handler._answer_query("g1", "复刻", "u1", "m1")
    assert sender.calls == [("text", "g1", "复刻排期暂不可用，请稍后再试", "m1")]


@pytest.mark.asyncio
async def test_plain_card_query_still_current_fast_path(tmp_path):
    handler, sender, data_dir = make_handler(tmp_path)
    await handler._answer_query("g1", "雾铃", "u1", "m1")
    assert len(sender.calls) == 1
    assert sender.calls[0][0] == "image"
    assert Path(sender.calls[0][1]) == card_path(data_dir, CID_W, 123)
    assert sender.calls[0][2] == ""


@pytest.mark.asyncio
async def test_plain_portrait_query_still_current(tmp_path):
    handler, sender, data_dir = make_handler(tmp_path)
    await handler._answer_query("g1", "雾铃1", "u1", "m1")
    assert len(sender.calls) == 1
    assert sender.calls[0][0] == "schedule_image"
    assert Path(sender.calls[0][1]) == portrait_path(data_dir, CID_W, SKIN_W, 123)
    assert sender.calls[0][2] == ""
