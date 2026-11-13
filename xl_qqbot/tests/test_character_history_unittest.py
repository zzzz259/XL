"""CharacterQuerier 内容历史：多版本图鉴聚合、内容折叠/回退/间断、stale/改名/旧格式回退与统一图片校验。

历史修订键为 card manifest 有效条目的 (data_sha256, artifact_sha256)；只折叠连续相同内容，
A→B→A 回退形成新节点，缺失版本造成的间断不跨合并。输出始终新到旧。
"""

import hashlib
import json
from pathlib import Path

from bot_app import querier as querier_module

CID = "10000101"
CHARACTERS = {
    "10000101": {"name": "菲尼斯/Finis", "star": 5},
    "10000224": {"name": "雾铃/Bell", "star": 4},
}

# 最小合法 PNG（8 字节签名 + IHDR + IEND；IHDR CRC 不校验）


def make_png(width: int, height: int) -> bytes:
    return (
        b"\x89PNG\r\n\x1a\n"
        + b"\x00\x00\x00\x0dIHDR"
        + width.to_bytes(4, "big")
        + height.to_bytes(4, "big")
        + b"\x08\x06\x00\x00\x00"
        + b"\x00\x00\x00\x00IEND\xaeB`\x82"
    )


PNG_A = make_png(2, 3)
PNG_B = make_png(4, 5)
PNG_C = make_png(6, 7)
SHA = {png: hashlib.sha256(png).hexdigest() for png in (PNG_A, PNG_B, PNG_C)}
D_A = hashlib.sha256(b"data-A").hexdigest()
D_B = hashlib.sha256(b"data-B").hexdigest()
D_C = hashlib.sha256(b"data-C").hexdigest()


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


def write_card_version(root, version, entries):
    """entries: list of dict(character_id, png, data_sha=None, state="published",
    source_version=None, artifact_path=None, sha256=None, filename=None, write=True)。"""
    version_dir = root / "versions" / str(version)
    cards_dir = version_dir / "character_cards"
    cards_dir.mkdir(parents=True, exist_ok=True)
    manifest = []
    for entry in entries:
        png = entry["png"]
        artifact_path = entry.get("artifact_path")
        if artifact_path is None:
            filename = entry.get("filename", f"{entry['character_id']}_角色档案_长图.png")
            artifact_path = f"character_cards/{filename}"
        if entry.get("write", True):
            target = version_dir / artifact_path
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(png)
        manifest.append(
            {
                "character_id": entry["character_id"],
                "state": entry.get("state", "published"),
                "artifact_path": artifact_path,
                "artifact_sha256": entry.get(
                    "sha256", hashlib.sha256(png).hexdigest()
                ),
                "data_sha256": entry["data_sha"] if "data_sha" in entry else hashlib.sha256(
                    f"data-{entry['character_id']}-{version}".encode()
                ).hexdigest(),
                "source_version": str(entry.get("source_version", version)),
            }
        )
    (cards_dir / "manifest.json").write_text(
        json.dumps(
            {"schema_version": 1, "version": str(version), "characters": manifest},
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )


def make_querier(root):
    return querier_module.CharacterQuerier(
        root / "character_data" / "current.json", root / "versions"
    )


def three_revision_root(tmp_path):
    """121 A → 122 B → 123 C，三个不同内容修订，指针 123。"""
    root = make_root(tmp_path, current=123)
    write_card_version(root, 121, [{"character_id": CID, "png": PNG_A, "data_sha": D_A}])
    write_card_version(root, 122, [{"character_id": CID, "png": PNG_B, "data_sha": D_B}])
    write_card_version(root, 123, [{"character_id": CID, "png": PNG_C, "data_sha": D_C}])
    return root


# ---------- 内容折叠 / 回退 / 间断 ----------


def test_fold_consecutive_identical_content(tmp_path):
    root = make_root(tmp_path, current=123)
    write_card_version(root, 121, [{"character_id": CID, "png": PNG_A, "data_sha": D_A}])
    write_card_version(root, 122, [{"character_id": CID, "png": PNG_A, "data_sha": D_A}])
    write_card_version(root, 123, [{"character_id": CID, "png": PNG_B, "data_sha": D_B}])
    querier = make_querier(root)

    current = querier.find_history(CID, offset=0)
    assert current.status == "found"
    assert current.character_id == CID
    assert current.name == "菲尼斯"
    assert current.artifact_sha256 == SHA[PNG_B]
    assert current.data_sha256 == D_B
    assert current.first_seen_version == "123"
    assert current.last_seen_version == "123"
    assert current.source_version == "123"
    assert current.total == 2
    assert current.stale is False
    assert current.image_path is not None and current.image_path.is_file()

    previous = querier.find_history(CID, offset=1)
    assert previous.status == "found"
    assert previous.artifact_sha256 == SHA[PNG_A]
    assert previous.first_seen_version == "121"
    assert previous.last_seen_version == "122"  # 连续相同折叠为一个修订
    assert previous.source_version == "121"
    assert "121" in previous.image_path.parts


def test_revert_same_content_is_new_revision(tmp_path):
    root = make_root(tmp_path, current=123)
    write_card_version(root, 121, [{"character_id": CID, "png": PNG_A, "data_sha": D_A}])
    write_card_version(root, 122, [{"character_id": CID, "png": PNG_B, "data_sha": D_B}])
    write_card_version(root, 123, [{"character_id": CID, "png": PNG_A, "data_sha": D_A}])
    querier = make_querier(root)

    assert querier.find_history(CID).total == 3
    newest = querier.find_history(CID, offset=0)
    oldest = querier.find_history(CID, offset=2)
    assert newest.artifact_sha256 == oldest.artifact_sha256  # 内容回退成相同字节
    assert newest.first_seen_version == "123"
    assert oldest.first_seen_version == "121"  # 但 A→B→A 仍形成新节点


def test_missing_version_gap_prevents_fold(tmp_path):
    root = make_root(tmp_path, current=124)
    write_card_version(root, 121, [{"character_id": CID, "png": PNG_A, "data_sha": D_A}])
    write_card_version(root, 122, [{"character_id": CID, "png": PNG_B, "data_sha": D_B}])
    # 123 的 manifest 存在但不含该角色：缺失造成间断，间断两侧相同内容也不合并
    write_card_version(
        root, 123, [{"character_id": "10000224", "png": PNG_C, "data_sha": D_C}]
    )
    write_card_version(root, 124, [{"character_id": CID, "png": PNG_A, "data_sha": D_A}])
    querier = make_querier(root)

    all_versions = querier.find_all_versions(CID)
    assert [r.first_seen_version for r in all_versions] == ["124", "122", "121"]
    assert [r.last_seen_version for r in all_versions] == ["124", "122", "121"]
    assert all_versions[0].artifact_sha256 == SHA[PNG_A]
    assert all_versions[2].artifact_sha256 == SHA[PNG_A]
    assert len(all_versions) == 3  # 不跨间断合并
    assert all(r.status == "found" for r in all_versions)


def test_history_is_newest_to_oldest(tmp_path):
    root = three_revision_root(tmp_path)
    querier = make_querier(root)
    seen = []
    for offset in range(3):
        result = querier.find_history(CID, offset=offset)
        assert result.status == "found"
        assert result.total == 3
        seen.append(result.first_seen_version)
    assert seen == ["123", "122", "121"]
    assert [querier.find_history(CID, offset=o).source_version for o in range(3)] == [
        "123",
        "122",
        "121",
    ]


def test_offset_zero_matches_current_find(tmp_path):
    root = three_revision_root(tmp_path)
    querier = make_querier(root)
    history = querier.find_history(CID)
    fast = querier.find(CID)
    assert history.status == "found"
    assert fast.status == "found"
    assert history.image_path == fast.image_path  # offset=0 就是当前有效内容


def test_find_all_versions_includes_current_newest_first(tmp_path):
    root = three_revision_root(tmp_path)
    querier = make_querier(root)
    all_versions = querier.find_all_versions(CID)
    assert len(all_versions) == 3
    assert all_versions[0].image_path == querier.find(CID).image_path
    assert all_versions[0].offset == 0
    assert [r.first_seen_version for r in all_versions] == ["123", "122", "121"]
    assert all(r.image_path is not None and r.image_path.is_file() for r in all_versions)


def test_offset_out_of_range_reports_total(tmp_path):
    root = three_revision_root(tmp_path)
    querier = make_querier(root)
    result = querier.find_history(CID, offset=3)
    assert result.status == "out_of_range"
    assert result.total == 3
    assert result.image_path is None

    single = make_root(tmp_path / "single", current=123)
    write_card_version(
        single, 123, [{"character_id": "10000224", "png": PNG_C, "data_sha": D_C}]
    )
    querier = make_querier(single)
    result = querier.find_history("10000224", offset=1)
    assert result.status == "out_of_range"
    assert result.total == 1


def test_revision_split_by_data_sha_change(tmp_path):
    root = make_root(tmp_path, current=122)
    write_card_version(root, 121, [{"character_id": CID, "png": PNG_A, "data_sha": D_A}])
    write_card_version(root, 122, [{"character_id": CID, "png": PNG_A, "data_sha": D_B}])
    querier = make_querier(root)
    assert querier.find_history(CID).total == 2  # 图相同但角色数据变化也是新修订
    assert querier.find_history(CID, offset=1).artifact_sha256 == SHA[PNG_A]


def test_revision_split_by_artifact_sha_change(tmp_path):
    root = make_root(tmp_path, current=122)
    write_card_version(root, 121, [{"character_id": CID, "png": PNG_A, "data_sha": D_A}])
    write_card_version(root, 122, [{"character_id": CID, "png": PNG_B, "data_sha": D_A}])
    querier = make_querier(root)
    assert querier.find_history(CID).total == 2  # 数据相同但重渲染出新图也是新修订


# ---------- stale / 删除 / 改名 ----------


def test_stale_entry_extends_revision_without_new_node(tmp_path):
    root = make_root(tmp_path, current=122)
    write_card_version(root, 121, [{"character_id": CID, "png": PNG_A, "data_sha": D_A}])
    write_card_version(
        root,
        122,
        [{"character_id": CID, "png": PNG_A, "data_sha": D_A,
          "state": "stale", "source_version": 121}],
    )
    querier = make_querier(root)
    result = querier.find_history(CID)
    assert result.status == "found"
    assert result.total == 1  # stale 仍指向旧内容，不制造新修订
    assert result.first_seen_version == "121"
    assert result.last_seen_version == "122"
    assert result.source_version == "121"
    assert result.stale is False  # 内容随当前版本有效


def test_character_removed_in_current_version_keeps_stale_history(tmp_path):
    root = make_root(tmp_path, current=122)
    write_card_version(root, 121, [{"character_id": CID, "png": PNG_A, "data_sha": D_A}])
    write_card_version(
        root, 122, [{"character_id": "10000224", "png": PNG_C, "data_sha": D_C}]
    )
    querier = make_querier(root)
    result = querier.find_history(CID)
    assert result.status == "found"
    assert result.total == 1
    assert result.first_seen_version == "121"
    assert result.last_seen_version == "121"
    assert result.stale is True  # 当前版本已删除，历史内容标记旧版


def test_rename_keeps_single_history_chain(tmp_path):
    root = make_root(tmp_path, current=122)
    write_card_version(root, 121, [{"character_id": CID, "png": PNG_A, "data_sha": D_A}])
    write_card_version(
        root,
        122,
        [{"character_id": CID, "png": PNG_B, "data_sha": D_B,
          "filename": "10000101_菲尼斯觉醒_角色档案_长图.png"}],
    )
    querier = make_querier(root)
    all_versions = querier.find_all_versions(CID)
    assert len(all_versions) == 2  # 按 character_id 聚合，文件名变化不影响身份
    assert all(r.character_id == CID for r in all_versions)


def test_legacy_version_without_manifest_falls_back_to_glob(tmp_path):
    root = make_root(tmp_path, current=122)
    legacy = root / "versions" / "121" / "character_cards"
    legacy.mkdir(parents=True)
    (legacy / "10000101_角色档案_长图.png").write_bytes(PNG_A)  # 旧格式：无 manifest
    write_card_version(root, 122, [{"character_id": CID, "png": PNG_B, "data_sha": D_B}])
    querier = make_querier(root)
    all_versions = querier.find_all_versions(CID)
    assert len(all_versions) == 2
    assert all_versions[1].image_path == legacy / "10000101_角色档案_长图.png"
    assert all_versions[1].data_sha256 == ""  # 旧格式没有 data_sha 可比对
    assert all_versions[1].first_seen_version == "121"


def test_pending_and_non_numeric_version_dirs_ignored(tmp_path):
    root = make_root(tmp_path, current=121)
    write_card_version(root, 121, [{"character_id": CID, "png": PNG_A, "data_sha": D_A}])
    # 工作/无效目录：若被当版本会多出内容或崩溃
    write_card_version(
        root, ".pending-120", [{"character_id": CID, "png": PNG_B, "data_sha": D_B}]
    )
    (root / "versions" / "abc").mkdir(parents=True)
    querier = make_querier(root)
    result = querier.find_history(CID)
    assert result.status == "found"
    assert result.total == 1
    assert result.artifact_sha256 == SHA[PNG_A]


# ---------- 历史图片统一校验 ----------


def _two_version_corrupt_root(tmp_path, **entry_overrides):
    """121 内容 A（好），122 内容 B（按测试要求损坏），指针 122。"""
    root = make_root(tmp_path, current=122)
    write_card_version(root, 121, [{"character_id": CID, "png": PNG_A, "data_sha": D_A}])
    entry = {"character_id": CID, "png": PNG_B, "data_sha": D_B}
    entry.update(entry_overrides)
    write_card_version(root, 122, [entry])
    return root


def test_history_path_traversal_rejected(tmp_path):
    root = _two_version_corrupt_root(tmp_path, artifact_path="../escape.png")
    (root / "versions" / "122" / "escape.png").write_bytes(PNG_B)  # 存在也必须拒
    querier = make_querier(root)
    result = querier.find_history(CID, offset=0)
    assert result.status == "unavailable"
    assert result.image_path is None
    assert result.error
    assert querier.find_history(CID, offset=1).status == "found"  # 修订编号不塌


def test_history_absolute_path_rejected(tmp_path):
    root = _two_version_corrupt_root(tmp_path, artifact_path="/etc/passwd")
    querier = make_querier(root)
    result = querier.find_history(CID)
    assert result.status == "unavailable"


def test_history_digest_mismatch_rejected(tmp_path):
    root = _two_version_corrupt_root(tmp_path, sha256="0" * 64)
    querier = make_querier(root)
    result = querier.find_history(CID)
    assert result.status == "unavailable"
    assert result.error


def test_history_non_png_rejected(tmp_path):
    root = _two_version_corrupt_root(tmp_path, png=b"not a png")
    querier = make_querier(root)
    result = querier.find_history(CID)
    assert result.status == "unavailable"


def test_history_missing_file_rejected(tmp_path):
    root = _two_version_corrupt_root(tmp_path, write=False)
    querier = make_querier(root)
    result = querier.find_history(CID)
    assert result.status == "unavailable"


def test_history_oversized_image_rejected(tmp_path, monkeypatch):
    from bot_app import history as history_module

    big_png = PNG_B + b"\x00" * 64  # 仍是合法 IHDR 的 PNG，但超过小阈值
    root = _two_version_corrupt_root(tmp_path, png=big_png)
    monkeypatch.setattr(history_module, "MAX_HISTORY_IMAGE_BYTES", 32)
    querier = make_querier(root)
    result = querier.find_history(CID)
    assert result.status == "unavailable"
    assert "32" in result.error
    monkeypatch.setattr(history_module, "MAX_HISTORY_IMAGE_BYTES", 4096)
    assert querier.find_history(CID).status == "found"


# ---------- 数据不可用 / 缓存 ----------


def test_character_without_any_version_entry_not_found(tmp_path):
    root = make_root(tmp_path)
    querier = make_querier(root)
    result = querier.find_history("99999999")
    assert result.status == "not_found"


def test_missing_versions_dir_unavailable(tmp_path):
    root = tmp_path / "data"
    root.mkdir()
    (root / "character_data").mkdir()
    (root / "character_data" / "current.json").write_text(
        json.dumps({"characters": CHARACTERS}, ensure_ascii=False), encoding="utf-8"
    )
    querier = make_querier(root)
    assert querier.find_history(CID).status == "unavailable"


def test_history_sees_new_version_after_pointer_change(tmp_path):
    root = make_root(tmp_path, current=123)
    write_card_version(root, 121, [{"character_id": CID, "png": PNG_A, "data_sha": D_A}])
    write_card_version(root, 122, [{"character_id": CID, "png": PNG_B, "data_sha": D_B}])
    querier = make_querier(root)
    assert querier.find_history(CID).total == 2
    write_card_version(root, 123, [{"character_id": CID, "png": PNG_C, "data_sha": D_C}])
    latest = querier.find_history(CID)
    assert latest.total == 3
    assert latest.first_seen_version == "123"
    assert latest.artifact_sha256 == SHA[PNG_C]


# ---------- 复审：损坏/不支持 schema 的 manifest 是 unavailable 修订，不是普通间断 ----------


def _write_raw_card_manifest(root, version, content):
    cards_dir = root / "versions" / str(version) / "character_cards"
    cards_dir.mkdir(parents=True, exist_ok=True)
    (cards_dir / "manifest.json").write_text(content, encoding="utf-8")


def test_history_manifest_corrupt_json_is_unavailable_not_gap(tmp_path):
    root = make_root(tmp_path, current=123)
    write_card_version(root, 121, [{"character_id": CID, "png": PNG_A, "data_sha": D_A}])
    _write_raw_card_manifest(root, 122, "{not json")
    write_card_version(root, 123, [{"character_id": CID, "png": PNG_C, "data_sha": D_C}])
    querier = make_querier(root)
    all_versions = querier.find_all_versions(CID)
    assert [r.status for r in all_versions] == ["found", "unavailable", "found"]
    assert all_versions[1].first_seen_version == "122"
    assert all_versions[1].error
    assert querier.find_history(CID, offset=1).status == "unavailable"
    assert querier.find_history(CID, offset=2).status == "found"


def test_history_manifest_unsupported_schema_is_unavailable(tmp_path):
    root = make_root(tmp_path, current=123)
    write_card_version(root, 121, [{"character_id": CID, "png": PNG_A, "data_sha": D_A}])
    bad = json.dumps(
        {"schema_version": 2, "version": "122", "characters": []}, ensure_ascii=False
    )
    _write_raw_card_manifest(root, 122, bad)
    write_card_version(root, 123, [{"character_id": CID, "png": PNG_C, "data_sha": D_C}])
    querier = make_querier(root)
    all_versions = querier.find_all_versions(CID)
    assert [r.status for r in all_versions] == ["found", "unavailable", "found"]
    assert "schema" in all_versions[1].error


def test_history_empty_artifact_sha_is_unavailable(tmp_path):
    root = make_root(tmp_path, current=122)
    write_card_version(root, 121, [{"character_id": CID, "png": PNG_A, "data_sha": D_A}])
    write_card_version(
        root, 122, [{"character_id": CID, "png": PNG_B, "data_sha": D_B, "sha256": ""}]
    )
    querier = make_querier(root)
    result = querier.find_history(CID)
    assert result.status == "unavailable"  # 空 SHA 不得跳过完整性校验
    assert "artifact_sha256" in result.error


def test_history_empty_data_sha_is_unavailable(tmp_path):
    root = make_root(tmp_path, current=122)
    write_card_version(root, 121, [{"character_id": CID, "png": PNG_A, "data_sha": D_A}])
    write_card_version(root, 122, [{"character_id": CID, "png": PNG_B, "data_sha": ""}])
    querier = make_querier(root)
    result = querier.find_history(CID)
    assert result.status == "unavailable"
    assert "data_sha256" in result.error


def test_history_missing_or_corrupt_pointer_unavailable(tmp_path):
    root = make_root(tmp_path, current=122)
    write_card_version(root, 121, [{"character_id": CID, "png": PNG_A, "data_sha": D_A}])
    (root / "current_version.json").unlink()
    querier = make_querier(root)
    assert querier.find_history(CID).status == "unavailable"
    assert querier.find_all_versions(CID)[0].status == "unavailable"
    (root / "current_version.json").write_text("{broken", encoding="utf-8")
    assert querier.find_history(CID).status == "unavailable"


def test_history_multiple_legacy_candidates_unavailable(tmp_path):
    root = make_root(tmp_path, current=122)
    legacy = root / "versions" / "121" / "character_cards"
    legacy.mkdir(parents=True)
    (legacy / "10000101_AAA_旧图_角色档案_长图.png").write_bytes(PNG_A)
    (legacy / "10000101_菲尼斯_角色档案_长图.png").write_bytes(PNG_B)
    write_card_version(root, 122, [{"character_id": CID, "png": PNG_C, "data_sha": D_C}])
    querier = make_querier(root)
    all_versions = querier.find_all_versions(CID)
    assert [r.status for r in all_versions] == ["found", "unavailable"]
    assert all_versions[1].first_seen_version == "121"
    assert "候选" in all_versions[1].error  # 多候选不猜第一张


def test_numeric_versions_entry_oserror_skipped(tmp_path, monkeypatch):
    root = make_root(tmp_path, current=122)
    write_card_version(root, 121, [{"character_id": CID, "png": PNG_A, "data_sha": D_A}])
    write_card_version(root, 122, [{"character_id": CID, "png": PNG_B, "data_sha": D_B}])
    real_is_dir = Path.is_dir

    def flaky_is_dir(self):
        if self.name == "122":
            raise OSError("simulated stat failure")
        return real_is_dir(self)

    monkeypatch.setattr(Path, "is_dir", flaky_is_dir)
    querier = make_querier(root)
    result = querier.find_history(CID)
    assert result.status == "found"  # 单条 OSError 被跳过并记日志，不拖垮查询
    assert result.total == 1
    assert result.first_seen_version == "121"


# ---------- 复审：普通当前 find() 优先 card manifest 映射 ----------


def test_find_prefers_manifest_mapping_over_glob(tmp_path):
    root = make_root(tmp_path, current=122)
    write_card_version(
        root,
        122,
        [{"character_id": CID, "png": PNG_B, "data_sha": D_B,
          "filename": "10000101_菲尼斯_角色档案_长图.png"}],
    )
    # 改名残留：ASCII 排序第一的旧图，glob 会错取它
    stale = (
        root / "versions" / "122" / "character_cards" / "10000101_AAA_旧图_角色档案_长图.png"
    )
    stale.write_bytes(PNG_A)
    querier = make_querier(root)
    result = querier.find(CID)
    assert result.status == "found"
    assert result.image_path.name == "10000101_菲尼斯_角色档案_长图.png"


def test_find_manifest_bad_sha_returns_no_image(tmp_path):
    root = make_root(tmp_path, current=122)
    write_card_version(
        root, 122, [{"character_id": CID, "png": PNG_B, "data_sha": D_B, "sha256": "0" * 64}]
    )
    querier = make_querier(root)
    result = querier.find(CID)
    assert result.status == "no_image"


def test_find_multiple_legacy_candidates_no_guess(tmp_path):
    root = make_root(tmp_path, current=122)
    cards = root / "versions" / "122" / "character_cards"
    cards.mkdir(parents=True)
    (cards / "10000101_AAA_旧图_角色档案_长图.png").write_bytes(PNG_A)
    (cards / "10000101_菲尼斯_角色档案_长图.png").write_bytes(PNG_B)
    querier = make_querier(root)
    result = querier.find(CID)
    assert result.status == "no_image"  # 无 manifest 时多候选也不随意取第一张
