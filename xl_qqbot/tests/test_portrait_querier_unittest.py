"""PortraitQuerier：当前版本 manifest、ordinal、缺图/旧图/越界、路径与哈希校验。"""

import hashlib
import json

import pytest

from bot_app import portrait_querier
from bot_app.portrait_querier import PortraitQueryResult, PortraitQuerier

CHARACTERS = {
    "10000101": {"name": "菲尼斯/Finis", "star": 5},
    "10000224": {"name": "雾铃/Bell", "star": 4},
}
SKINS = [
    # 雾铃 3 套：游戏顺序编号，中间夹一个 failed 不压缩后续编号
    {"character_id": "10000224", "skin_id": "1000022401", "ordinal": 1,
     "name": "雾铃", "state": "published"},
    {"character_id": "10000224", "skin_id": "1000022402", "ordinal": 2,
     "name": "雾铃·夜语", "state": "failed", "error": "render boom"},
    {"character_id": "10000224", "skin_id": "1000022403", "ordinal": 3,
     "name": "雾铃·晨曦", "state": "not_generated"},
    {"character_id": "10000224", "skin_id": "1000022404", "ordinal": 4,
     "name": "雾铃·旧梦", "state": "stale", "source_version": 122},
    # 菲尼斯 2 套
    {"character_id": "10000101", "skin_id": "1000010101", "ordinal": 1,
     "name": "菲尼斯", "state": "published"},
    {"character_id": "10000101", "skin_id": "1000010102", "ordinal": 2,
     "name": "菲尼斯·觉醒", "state": "published"},
]

# 最小合法 PNG：8 字节签名 + IHDR（宽 2 高 3）+ IEND
PNG_2X3 = (
    b"\x89PNG\r\n\x1a\n"
    + b"\x00\x00\x00\x0dIHDR"
    + b"\x00\x00\x00\x02\x00\x00\x00\x03\x08\x06\x00\x00\x00"
    + b"\x00\x00\x00\x00IEND\xaeB`\x82"
)


def _write_manifest(root, version, entries):
    manifest_dir = root / "versions" / str(version) / "portraits"
    manifest_dir.mkdir(parents=True, exist_ok=True)
    (root / "current_version.json").write_text(
        json.dumps({"version": version}), encoding="utf-8"
    )
    (root / "character_data").mkdir(parents=True, exist_ok=True)
    (root / "character_data" / "current.json").write_text(
        json.dumps({"characters": CHARACTERS}, ensure_ascii=False), encoding="utf-8"
    )
    skins = []
    for entry in entries:
        item = {
            "character_id": entry["character_id"],
            "skin_id": entry["skin_id"],
            "ordinal": entry["ordinal"],
            "name": entry["name"],
            "input_fingerprint": f"fp-{entry['skin_id']}",
            "state": entry["state"],
            "artifact_path": None,
            "artifact_sha256": None,
            "source_version": entry.get("source_version"),
            "width": None,
            "height": None,
            "error": entry.get("error"),
        }
        if entry["state"] in ("published", "stale"):
            png = PNG_2X3
            item["artifact_path"] = f"{entry['character_id']}/{entry['skin_id']}.png"
            item["artifact_sha256"] = hashlib.sha256(png).hexdigest()
            item["width"] = 2
            item["height"] = 3
            if entry["state"] == "published":
                item["source_version"] = version
            sub = manifest_dir / entry["character_id"]
            sub.mkdir(parents=True, exist_ok=True)
            (sub / f"{entry['skin_id']}.png").write_bytes(png)
        skins.append(item)
    (manifest_dir / "manifest.json").write_text(
        json.dumps({"schema_version": 1, "version": str(version), "skins": skins}),
        encoding="utf-8",
    )


def make_querier(tmp_path, version=123):
    root = tmp_path / "server_data"
    _write_manifest(root, version, SKINS)
    return PortraitQuerier(root / "character_data" / "current.json", root / "versions")


# ---------- 正常命中 ----------

def test_published_current_ordinal(tmp_path):
    querier = make_querier(tmp_path)
    result = querier.find("10000224", 1)
    assert result.status == "found"
    assert result.character_id == "10000224"
    assert result.skin_id == "1000022401"
    assert result.name == "雾铃"
    assert result.stale is False
    assert result.available_range == (1, 4)
    assert result.image_path is not None and result.image_path.is_file()
    assert result.width == 2 and result.height == 3
    assert result.source_version == "123"


def test_ordinal_is_per_character(tmp_path):
    querier = make_querier(tmp_path)
    result = querier.find("10000101", 2)
    assert result.status == "found"
    assert result.skin_id == "1000010102"


def test_manifest_reloaded_after_pointer_change(tmp_path):
    root = tmp_path / "server_data"
    _write_manifest(root, 123, SKINS)
    querier = PortraitQuerier(root / "character_data" / "current.json", root / "versions")
    assert querier.find("10000101", 1).status == "found"
    _write_manifest(root, 124, SKINS)
    assert querier.find("10000101", 1).image_path.parent.parent.parent.name == "124"


# ---------- 未生成/失败/旧图 ----------

def test_not_generated_only_notice(tmp_path):
    querier = make_querier(tmp_path)
    result = querier.find("10000224", 3)
    assert result.status == "not_generated"
    assert result.image_path is None
    assert result.available_range == (1, 4)


def test_failed_only_notice(tmp_path):
    querier = make_querier(tmp_path)
    result = querier.find("10000224", 2)
    assert result.status == "failed"
    assert result.image_path is None
    assert result.error == "render boom"


def test_not_applicable_only_notice(tmp_path):
    querier = make_querier(tmp_path)
    _patch_manifest(tmp_path, lambda payload: payload["skins"][2].update(
        state="not_applicable", error="当前版本不适用"))
    result = querier.find("10000224", 3)
    assert result.status == "not_applicable"
    assert result.image_path is None
    assert result.error == "当前版本不适用"


def test_stale_returns_old_image(tmp_path):
    querier = make_querier(tmp_path)
    result = querier.find("10000224", 4)
    assert result.status == "found"
    assert result.stale is True
    assert result.source_version == "122"


def test_no_ordinal_fallback_to_1(tmp_path):
    querier = make_querier(tmp_path)
    assert querier.find("10000224").skin_id == "1000022401"
    assert querier.find("10000224", None).skin_id == "1000022401"


# ---------- 越界 ----------

def test_out_of_range_below_and_above(tmp_path):
    querier = make_querier(tmp_path)
    for ordinal in (0, 5, 99):
        result = querier.find("10000224", ordinal)
        assert result.status == "out_of_range"
        assert result.available_range == (1, 4)
    assert querier.find("10000101", 3).status == "out_of_range"
    assert querier.find("10000101", 3).available_range == (1, 2)


# ---------- 数据不可用 ----------

def test_character_without_any_skin(tmp_path):
    querier = make_querier(tmp_path)
    result = querier.find("99999999", 1)
    assert result.status == "not_found"


def test_missing_pointer(tmp_path):
    root = tmp_path / "server_data"
    root.mkdir()
    querier = PortraitQuerier(root / "character_data" / "current.json", root / "versions")
    assert querier.find("10000224", 1).status == "unavailable"


def test_missing_manifest(tmp_path):
    root = tmp_path / "server_data"
    (root / "versions" / "123").mkdir(parents=True)
    (root / "current_version.json").write_text('{"version": 123}', encoding="utf-8")
    querier = PortraitQuerier(root / "character_data" / "current.json", root / "versions")
    assert querier.find("10000224", 1).status == "unavailable"


# ---------- 校验：路径、哈希、PNG 头、尺寸 ----------

def _patch_manifest(tmp_path, mutator):
    manifest = tmp_path / "server_data" / "versions" / "123" / "portraits" / "manifest.json"
    payload = json.loads(manifest.read_text(encoding="utf-8"))
    mutator(payload)
    manifest.write_text(json.dumps(payload), encoding="utf-8")


def test_path_traversal_rejected(tmp_path):
    querier = make_querier(tmp_path)
    _patch_manifest(tmp_path, lambda p: p["skins"][0].update(
        artifact_path="../../escape.png"))
    assert querier.find("10000224", 1).status == "unavailable"


def test_absolute_path_rejected(tmp_path):
    querier = make_querier(tmp_path)
    _patch_manifest(tmp_path, lambda p: p["skins"][0].update(
        artifact_path="/etc/passwd"))
    assert querier.find("10000224", 1).status == "unavailable"


def test_digest_mismatch_rejected(tmp_path):
    querier = make_querier(tmp_path)
    _patch_manifest(tmp_path, lambda p: p["skins"][0].update(
        artifact_sha256="0" * 64))
    assert querier.find("10000224", 1).status == "unavailable"


def test_non_png_rejected(tmp_path):
    querier = make_querier(tmp_path)

    def corrupt(p):
        skin = p["skins"][0]
        png = tmp_path / "server_data" / "versions" / "123" / "portraits" / skin["artifact_path"]
        png.write_bytes(b"not a png")
        skin["artifact_sha256"] = hashlib.sha256(b"not a png").hexdigest()

    _patch_manifest(tmp_path, corrupt)
    assert querier.find("10000224", 1).status == "unavailable"


def test_png_size_mismatch_rejected(tmp_path):
    querier = make_querier(tmp_path)
    _patch_manifest(tmp_path, lambda p: p["skins"][0].update(width=999, height=999))
    assert querier.find("10000224", 1).status == "unavailable"


def test_missing_png_file_rejected(tmp_path):
    querier = make_querier(tmp_path)

    def delete(p):
        skin = p["skins"][0]
        (tmp_path / "server_data" / "versions" / "123" / "portraits" / skin["artifact_path"]).unlink()

    _patch_manifest(tmp_path, delete)
    assert querier.find("10000224", 1).status == "unavailable"


# ---------- QQ 图片 20MiB 硬限 ----------

def test_oversized_image_is_unavailable_with_actionable_error(tmp_path, monkeypatch):
    """超过 QQ 20MiB 硬限（测试用小阈值）的立绘：unavailable + 可行动错误，不交给 Gateway。"""
    querier = make_querier(tmp_path)
    monkeypatch.setattr(portrait_querier, "MAX_PORTRAIT_IMAGE_BYTES", 32)
    result = querier.find("10000224", 1)
    assert result.status == "unavailable"
    assert result.image_path is None
    assert "32" in result.error  # 可行动：指明上限与超限方向
    assert str(len(PNG_2X3)) in result.error  # stat 先行：报错里的字节数来自 stat

    # 阈值放宽到文件大小以上 → 恢复正常命中，真实样本不受影响
    monkeypatch.setattr(portrait_querier, "MAX_PORTRAIT_IMAGE_BYTES", 1024)
    ok = querier.find("10000224", 1)
    assert ok.status == "found"
    assert ok.image_path is not None


# ---------- 备用发送入口 ----------

def test_result_has_name_for_display():
    result = PortraitQueryResult(
        status="found",
        image_path=__import__("pathlib").Path("/x/10000224_1000022401_abcd.png"),
        name="雾铃",
    )
    assert result.name == "雾铃"
