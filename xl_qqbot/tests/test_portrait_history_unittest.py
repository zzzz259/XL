"""PortraitQuerier 内容历史：当前 ordinal 固定到 skin_id 后跨版本追踪，内容折叠/回退/间断、
stale/basic 与和图鉴统一的历史图片校验（路径/SHA/PNG/IHDR 尺寸/20MiB）。

修订键为解码后像素内容 SHA；stale 指向旧内容不制造新修订；输出始终新到旧。
同一像素内容即使压缩字节/文件 SHA 不同也视为同一修订；artifact_sha256 仅用于完整性校验。
"""

import hashlib
import json
import struct
import zlib

from bot_app import portrait_querier as portrait_querier_module

CID = "10000224"
CHARACTERS = {
    "10000101": {"name": "菲尼斯/Finis", "star": 5},
    "10000224": {"name": "雾铃/Bell", "star": 4},
}
SKIN_1 = "1000022401"
SKIN_2 = "1000022402"


def _png_chunk(tag: bytes, payload: bytes) -> bytes:
    return (
        struct.pack(">I", len(payload))
        + tag
        + payload
        + struct.pack(">I", zlib.crc32(tag + payload) & 0xFFFFFFFF)
    )


def make_png(width: int, height: int, rgba=(1, 2, 3, 4), variant: int = 0) -> bytes:
    """RGBA8、无隔行的合法 PNG；variant=1 重打包为不同文件字节、解码像素相同。"""
    row = bytes(rgba) * width
    if variant == 0:
        raw = b"".join(b"\x00" + row for _ in range(height))
        level = 6
    else:
        first = row[:4]
        rest = bytes((row[i] - row[i - 4]) & 0xFF for i in range(4, len(row)))
        sub_row = first + rest
        raw = b"".join(b"\x01" + sub_row for _ in range(height))
        level = 9
    ihdr = struct.pack(">IIBBBBB", width, height, 8, 6, 0, 0, 0)
    return (
        b"\x89PNG\r\n\x1a\n"
        + _png_chunk(b"IHDR", ihdr)
        + _png_chunk(b"IDAT", zlib.compress(raw, level))
        + _png_chunk(b"IEND", b"")
    )


P1 = make_png(2, 3)
P2 = make_png(4, 5)
P3 = make_png(6, 7)
X1 = make_png(8, 9)
SHA = {png: hashlib.sha256(png).hexdigest() for png in (P1, P2, P3, X1)}


def make_root(tmp_path, current=123):
    root = tmp_path / "data"
    (root / "character_data").mkdir(parents=True)
    (root / "character_data" / "current.json").write_text(
        json.dumps({"characters": CHARACTERS}, ensure_ascii=False), encoding="utf-8"
    )
    (root / "current_version.json").write_text(
        json.dumps({"version": current}), encoding="utf-8"
    )
    (root / "versions").mkdir()
    return root


def write_portrait_version(root, version, skins):
    """skins: list of dict(character_id, skin_id, ordinal, name, state="published",
    png=None, source_version=None, sha256=None, artifact_path=None, write=True,
    render_scope="full", render_notice=None, width=None, height=None, error=None)。"""
    pdir = root / "versions" / str(version) / "portraits"
    pdir.mkdir(parents=True, exist_ok=True)
    items = []
    for skin in skins:
        item = {
            "character_id": skin["character_id"],
            "skin_id": skin["skin_id"],
            "ordinal": skin["ordinal"],
            "name": skin["name"],
            "input_fingerprint": f"fp-{skin['skin_id']}",
            "state": skin["state"],
            "artifact_path": None,
            "artifact_sha256": None,
            "source_version": None,
            "width": None,
            "height": None,
            "error": skin.get("error"),
            "render_scope": skin.get("render_scope", "full"),
            "render_notice": skin.get("render_notice"),
        }
        png = skin.get("png")
        if png is not None:
            if "artifact_path" in skin:
                artifact_path = skin["artifact_path"]
            else:
                artifact_path = f"{skin['character_id']}/{skin['skin_id']}.png"
            if skin.get("write", True):
                sub = pdir / skin["character_id"]
                sub.mkdir(parents=True, exist_ok=True)
                (sub / f"{skin['skin_id']}.png").write_bytes(png)
            item["artifact_path"] = artifact_path
            item["artifact_sha256"] = skin.get(
                "sha256", hashlib.sha256(png).hexdigest()
            )
            item["source_version"] = str(skin.get("source_version", version))
            item["width"] = skin.get("width", int.from_bytes(png[16:20], "big"))
            item["height"] = skin.get("height", int.from_bytes(png[20:24], "big"))
        items.append(item)
    (pdir / "manifest.json").write_text(
        json.dumps(
            {"schema_version": 1, "version": str(version), "skins": items},
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )


def make_querier(root):
    return portrait_querier_module.PortraitQuerier(
        root / "character_data" / "current.json", root / "versions"
    )


def skin(skin_id, ordinal, png, **overrides):
    entry = {
        "character_id": CID,
        "skin_id": skin_id,
        "ordinal": ordinal,
        "name": f"皮肤{skin_id[-2:]}",
        "state": "published",
        "png": png,
    }
    entry.update(overrides)
    return entry


def three_revision_root(tmp_path):
    """121 P1 → 122 P2 → 123 P3，皮肤 SKIN_1 的三个内容修订。"""
    root = make_root(tmp_path, current=123)
    write_portrait_version(root, 121, [skin(SKIN_1, 1, P1)])
    write_portrait_version(root, 122, [skin(SKIN_1, 1, P2)])
    write_portrait_version(root, 123, [skin(SKIN_1, 1, P3)])
    return root


# ---------- ordinal → skin_id 固定追踪 ----------


def test_ordinal_pinned_to_skin_id_across_reorder(tmp_path):
    root = make_root(tmp_path, current=122)
    write_portrait_version(
        root,
        121,
        [skin(SKIN_1, 1, P1, name="雾铃"), skin(SKIN_2, 2, X1, name="雾铃·夜语")],
    )
    write_portrait_version(
        root,
        122,
        [skin(SKIN_2, 1, P2, name="雾铃·夜语"), skin(SKIN_1, 2, P1, name="雾铃")],
    )
    querier = make_querier(root)

    # 当前 ordinal 1 = SKIN_2；历史必须追踪 SKIN_2，不能按各版 ordinal 1 串到 SKIN_1
    current = querier.find_history(CID, 1, offset=0)
    assert current.status == "found"
    assert current.skin_id == SKIN_2
    assert current.image_path.name == f"{SKIN_2}.png"
    assert current.first_seen_version == "122"
    assert current.last_seen_version == "122"

    older = querier.find_history(CID, 1, offset=1)
    assert older.status == "found"
    assert older.skin_id == SKIN_2  # 121 里 SKIN_2 是 ordinal 2，仍要取到它
    assert older.image_path.name == f"{SKIN_2}.png"
    assert "121" in older.image_path.parts
    assert older.image_path.read_bytes() == X1

    other = querier.find_history(CID, 2, offset=0)
    assert other.skin_id == SKIN_1  # 另一个序号追踪另一套皮肤，互不串台


def test_reorder_keeps_distinct_skin_histories(tmp_path):
    root = make_root(tmp_path, current=122)
    write_portrait_version(
        root,
        121,
        [skin(SKIN_1, 1, P1), skin(SKIN_2, 2, X1)],
    )
    write_portrait_version(
        root,
        122,
        [skin(SKIN_2, 1, P2), skin(SKIN_1, 2, P1)],
    )
    querier = make_querier(root)
    assert querier.find_history(CID, 1).total == 2
    assert querier.find_history(CID, 2).total == 1  # SKIN_1 内容两版相同，折叠


# ---------- 折叠 / 回退 / 间断 ----------


def test_fold_consecutive_identical_skin_content(tmp_path):
    root = make_root(tmp_path, current=122)
    write_portrait_version(root, 121, [skin(SKIN_1, 1, P1)])
    write_portrait_version(root, 122, [skin(SKIN_1, 1, P1)])
    querier = make_querier(root)
    result = querier.find_history(CID, 1)
    assert result.status == "found"
    assert result.total == 1
    assert result.first_seen_version == "121"
    assert result.last_seen_version == "122"


def test_skin_revert_ab_a_creates_new_revision(tmp_path):
    root = make_root(tmp_path, current=123)
    write_portrait_version(root, 121, [skin(SKIN_1, 1, P1)])
    write_portrait_version(root, 122, [skin(SKIN_1, 1, P2)])
    write_portrait_version(root, 123, [skin(SKIN_1, 1, P1)])
    querier = make_querier(root)
    assert querier.find_history(CID, 1).total == 3
    assert querier.find_history(CID, 1, offset=0).artifact_sha256 == SHA[P1]
    assert querier.find_history(CID, 1, offset=2).first_seen_version == "121"


def test_skin_gap_not_folded(tmp_path):
    root = make_root(tmp_path, current=124)
    write_portrait_version(root, 121, [skin(SKIN_1, 1, P1)])
    write_portrait_version(root, 122, [skin(SKIN_1, 1, P2)])
    # 123 该皮肤缺失（只有另一套皮肤），间断两侧相同内容不合并
    write_portrait_version(root, 123, [skin(SKIN_2, 2, X1)])
    write_portrait_version(root, 124, [skin(SKIN_1, 1, P1)])
    querier = make_querier(root)
    all_versions = querier.find_all_versions(CID, 1)
    assert [r.first_seen_version for r in all_versions] == ["124", "122", "121"]
    assert all_versions[0].artifact_sha256 == SHA[P1]
    assert all_versions[2].artifact_sha256 == SHA[P1]
    assert len(all_versions) == 3


def test_find_all_versions_newest_first(tmp_path):
    root = three_revision_root(tmp_path)
    querier = make_querier(root)
    all_versions = querier.find_all_versions(CID, 1)
    assert len(all_versions) == 3
    assert [r.first_seen_version for r in all_versions] == ["123", "122", "121"]
    assert all(r.status == "found" for r in all_versions)
    assert all_versions[0].image_path.read_bytes() == P3
    assert all_versions[2].image_path.read_bytes() == P1


def test_offset_out_of_range(tmp_path):
    root = three_revision_root(tmp_path)
    querier = make_querier(root)
    result = querier.find_history(CID, 1, offset=3)
    assert result.status == "out_of_range"
    assert result.total == 3
    assert result.image_path is None


def test_ordinal_out_of_range_reports_available(tmp_path):
    root = make_root(tmp_path, current=122)
    write_portrait_version(
        root, 122, [skin(SKIN_1, 1, P1), skin(SKIN_2, 2, X1)]
    )
    querier = make_querier(root)
    result = querier.find_history(CID, 9)
    assert result.status == "out_of_range"
    assert result.available_range == (1, 2)


# ---------- stale / basic / 未生成 ----------


def test_stale_entry_extends_revision(tmp_path):
    root = make_root(tmp_path, current=122)
    write_portrait_version(root, 121, [skin(SKIN_1, 1, P1)])
    write_portrait_version(
        root, 122, [skin(SKIN_1, 1, P1, state="stale", source_version=121)]
    )
    querier = make_querier(root)
    result = querier.find_history(CID, 1)
    assert result.status == "found"
    assert result.total == 1  # stale 不制造新修订
    assert result.first_seen_version == "121"
    assert result.last_seen_version == "122"
    assert result.source_version == "121"
    assert result.stale is False


def test_skin_not_generated_in_current_uses_old_content_stale(tmp_path):
    root = make_root(tmp_path, current=122)
    write_portrait_version(root, 121, [skin(SKIN_1, 1, P1)])
    write_portrait_version(
        root, 122, [skin(SKIN_1, 1, None, state="not_generated")]
    )
    querier = make_querier(root)
    result = querier.find_history(CID, 1)
    assert result.status == "found"
    assert result.total == 1
    assert result.last_seen_version == "121"
    assert result.stale is True  # 最新可用内容停留在旧版本


def test_basic_render_scope_preserved_in_history(tmp_path):
    root = make_root(tmp_path, current=122)
    write_portrait_version(
        root,
        121,
        [skin(SKIN_1, 1, P1, render_scope="basic", render_notice="unsupported particles")],
    )
    write_portrait_version(
        root,
        122,
        [skin(SKIN_1, 1, P2, render_scope="basic", render_notice="unsupported particles")],
    )
    querier = make_querier(root)
    result = querier.find_history(CID, 1)
    assert result.status == "found"
    assert result.render_scope == "basic"
    assert result.render_notice == "unsupported particles"


def test_skin_without_any_image_not_found(tmp_path):
    root = make_root(tmp_path, current=123)
    write_portrait_version(
        root, 123, [skin(SKIN_1, 1, None, state="not_generated")]
    )
    querier = make_querier(root)
    result = querier.find_history(CID, 1)
    assert result.status == "not_found"
    assert result.image_path is None


# ---------- 历史图片统一校验（损坏发生在旧版本） ----------


def _two_version_corrupt_root(tmp_path, **skin_overrides):
    """121 内容 P1（好），122 内容 P2（按测试要求损坏），指针 122。"""
    root = make_root(tmp_path, current=122)
    write_portrait_version(root, 121, [skin(SKIN_1, 1, P1)])
    entry = skin(SKIN_1, 1, P2)
    entry.update(skin_overrides)
    write_portrait_version(root, 122, [entry])
    return root


def test_history_traversal_rejected(tmp_path):
    root = _two_version_corrupt_root(tmp_path, artifact_path="../escape.png")
    (root / "versions" / "122" / "escape.png").write_bytes(P2)  # 存在也必须拒
    querier = make_querier(root)
    result = querier.find_history(CID, 1, offset=0)
    assert result.status == "unavailable"
    assert result.image_path is None
    assert result.error
    assert querier.find_history(CID, 1, offset=1).status == "found"


def test_history_digest_mismatch_rejected(tmp_path):
    root = _two_version_corrupt_root(tmp_path, sha256="0" * 64)
    querier = make_querier(root)
    result = querier.find_history(CID, 1)
    assert result.status == "unavailable"


def test_history_non_png_rejected(tmp_path):
    root = _two_version_corrupt_root(tmp_path, png=b"not a png")
    querier = make_querier(root)
    result = querier.find_history(CID, 1)
    assert result.status == "unavailable"


def test_history_size_mismatch_rejected(tmp_path):
    root = _two_version_corrupt_root(tmp_path, width=999, height=999)
    querier = make_querier(root)
    result = querier.find_history(CID, 1)
    assert result.status == "unavailable"


def test_history_missing_file_rejected(tmp_path):
    root = _two_version_corrupt_root(tmp_path, write=False)
    querier = make_querier(root)
    result = querier.find_history(CID, 1)
    assert result.status == "unavailable"


def test_history_oversized_rejected(tmp_path, monkeypatch):
    from bot_app import history as history_module

    big_png = P2 + b"\x00" * 64  # 仍是合法 IHDR 的 PNG，但超过小阈值
    root = _two_version_corrupt_root(tmp_path, png=big_png)
    monkeypatch.setattr(history_module, "MAX_HISTORY_IMAGE_BYTES", 32)
    querier = make_querier(root)
    result = querier.find_history(CID, 1)
    assert result.status == "unavailable"
    assert "32" in result.error
    monkeypatch.setattr(history_module, "MAX_HISTORY_IMAGE_BYTES", 4096)
    assert querier.find_history(CID, 1).status == "found"


# ---------- 数据不可用 / 缓存 ----------


def test_missing_pointer_unavailable(tmp_path):
    root = tmp_path / "data"
    root.mkdir()
    (root / "character_data").mkdir()
    (root / "character_data" / "current.json").write_text(
        json.dumps({"characters": CHARACTERS}, ensure_ascii=False), encoding="utf-8"
    )
    querier = make_querier(root)
    assert querier.find_history(CID, 1).status == "unavailable"


def test_refresh_after_pointer_change(tmp_path):
    root = make_root(tmp_path, current=122)
    write_portrait_version(root, 121, [skin(SKIN_1, 1, P1)])
    write_portrait_version(root, 122, [skin(SKIN_1, 1, P2)])
    querier = make_querier(root)
    assert querier.find_history(CID, 1).total == 2
    write_portrait_version(root, 123, [skin(SKIN_1, 1, P3)])
    (root / "current_version.json").write_text(
        json.dumps({"version": 123}), encoding="utf-8"
    )
    latest = querier.find_history(CID, 1)
    assert latest.total == 3
    assert latest.first_seen_version == "123"
    assert latest.artifact_sha256 == SHA[P3]


def test_missing_manifest_not_backfilled_is_skipped_without_splitting_content(tmp_path):
    root = make_root(tmp_path, current=124)
    write_portrait_version(root, 121, [skin(SKIN_1, 1, P1)])
    write_portrait_version(root, 122, [skin(SKIN_1, 1, P2)])
    # 123 是生产已有但没有历史 Arts 索引的版本：没有 portraits 目录，不是内容变化。
    (root / "versions" / "123" / "character_data").mkdir(parents=True)
    write_portrait_version(root, 124, [skin(SKIN_1, 1, P2)])
    querier = make_querier(root)

    all_versions = querier.find_all_versions(CID, 1)
    assert [r.status for r in all_versions] == ["found", "found"]
    assert all_versions[0].artifact_sha256 == SHA[P2]
    assert all_versions[0].first_seen_version == "122"
    assert all_versions[0].last_seen_version == "124"
    assert all_versions[1].artifact_sha256 == SHA[P1]
    assert not any(r.status == "unavailable" for r in all_versions)


# ---------- 复审：损坏/不支持 schema 的 portraits manifest 是 unavailable 修订 ----------


def _write_raw_portrait_manifest(root, version, content):
    pdir = root / "versions" / str(version) / "portraits"
    pdir.mkdir(parents=True, exist_ok=True)
    (pdir / "manifest.json").write_text(content, encoding="utf-8")


def test_history_manifest_corrupt_json_is_unavailable_not_gap(tmp_path):
    root = make_root(tmp_path, current=123)
    write_portrait_version(root, 121, [skin(SKIN_1, 1, P1)])
    _write_raw_portrait_manifest(root, 122, "{not json")
    write_portrait_version(root, 123, [skin(SKIN_1, 1, P3)])
    querier = make_querier(root)
    all_versions = querier.find_all_versions(CID, 1)
    assert [r.status for r in all_versions] == ["found", "unavailable", "found"]
    assert all_versions[1].first_seen_version == "122"
    assert all_versions[1].error
    assert querier.find_history(CID, 1, offset=1).status == "unavailable"
    assert querier.find_history(CID, 1, offset=2).status == "found"


def test_history_manifest_unsupported_schema_is_unavailable(tmp_path):
    root = make_root(tmp_path, current=123)
    write_portrait_version(root, 121, [skin(SKIN_1, 1, P1)])
    bad = json.dumps({"schema_version": 2, "version": "122", "skins": []}, ensure_ascii=False)
    _write_raw_portrait_manifest(root, 122, bad)
    write_portrait_version(root, 123, [skin(SKIN_1, 1, P3)])
    querier = make_querier(root)
    result = querier.find_history(CID, 1, offset=1)
    assert result.status == "unavailable"
    assert "schema" in result.error


def test_history_empty_artifact_sha_is_unavailable(tmp_path):
    root = _two_version_corrupt_root(tmp_path, sha256="")
    querier = make_querier(root)
    result = querier.find_history(CID, 1)
    assert result.status == "unavailable"  # 空 SHA 不得跳过完整性校验
    assert "artifact_sha256" in result.error


def test_history_empty_artifact_path_is_unavailable(tmp_path):
    root = make_root(tmp_path, current=122)
    write_portrait_version(root, 121, [skin(SKIN_1, 1, P1)])
    write_portrait_version(root, 122, [skin(SKIN_1, 1, P2, artifact_path="", write=False)])
    querier = make_querier(root)
    result = querier.find_history(CID, 1)
    assert result.status == "unavailable"
    assert "artifact_path" in result.error


# ---------- 像素级折叠键：同像素不同压缩字节/文件 SHA 必须折叠 ----------


def test_fold_same_pixels_different_file_bytes(tmp_path):
    p1v = make_png(2, 3, variant=1)
    assert hashlib.sha256(p1v).hexdigest() != SHA[P1]  # 文件 SHA 不同
    root = make_root(tmp_path, current=122)
    write_portrait_version(root, 121, [skin(SKIN_1, 1, P1)])
    write_portrait_version(root, 122, [skin(SKIN_1, 1, p1v)])
    querier = make_querier(root)
    result = querier.find_history(CID, 1)
    assert result.status == "found"
    assert result.total == 1  # 同像素 → 折叠为一个修订
    assert result.first_seen_version == "121"
    assert result.last_seen_version == "122"


def test_same_size_different_pixels_split_revision(tmp_path):
    px = make_png(3, 3, rgba=(1, 1, 1, 255))
    py = make_png(3, 3, rgba=(2, 2, 2, 255))
    root = make_root(tmp_path, current=122)
    write_portrait_version(root, 121, [skin(SKIN_1, 1, px)])
    write_portrait_version(root, 122, [skin(SKIN_1, 1, py)])
    assert make_querier(root).find_history(CID, 1).total == 2  # 同尺寸不同像素仍分修订
