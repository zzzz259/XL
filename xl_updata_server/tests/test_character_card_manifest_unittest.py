"""每版本图鉴有效产物 manifest 的契约测试（实施计划 Task 2）。

目标模块 ``server_app.character_card_manifest`` 负责在版本 staging 的
``character_cards/`` 目录写入当版每个有效角色的 manifest（schema 1）::

    SCHEMA_VERSION = 1

    def write_card_manifest(
        cards_dir: Path,
        *,
        version: int | str,
        characters: Mapping[str | int, Mapping[str, Any]],
        card_report: CardExportReport,
        previous_manifest: Mapping[str, Any] | None,
    ) -> dict:
        '''原子写入 cards_dir/manifest.json 并返回内容。

        顶层: {"schema_version": 1, "version": str(version),
               "characters": [entry, ...]}，entry 按 character_id 排序：
        {"character_id", "state": "published" | "stale",
         "artifact_path": "character_cards/<文件名>.png" | None,
         "artifact_sha256": str | None, "data_sha256": str,
         "source_version": str, "error": str | None}

        - 渲染成功（出现在 card_report.character_outputs）：state=published，
          source_version=当前版本，artifact 哈希取实际 PNG 字节；
        - data_sha256 为角色全字段规范化 JSON
          （json.dumps sort_keys ensure_ascii=False default=str）的 SHA-256，
          与 processor._canonical_character 同一口径；
        - 内容未变化（与上一版 data_sha256 相同且无新产物）：继承上一版条目，
          保留原 source_version / artifact / 哈希；
        - 内容变化但渲染失败（card_report.failures）：保留旧有效内容并标
          stale，artifact 与 data 哈希维持旧值，不得把旧图伪装成新修订；
        - 当前版本已删除的角色不产生条目，即使旧 PNG 仍留在目录中。
        '''

    def load_card_manifest(cards_dir: Path) -> dict | None
    def validate_card_manifest(payload: Mapping, cards_dir: Path) -> tuple[str, ...]
        '''校验路径相对且不出版本根、文件存在、artifact_sha256 与实际字节一致。'''

旧的版本目录没有 card manifest 时由回填/迁移程序补建；本模块不参与 Bot 查询回退。
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

from server_app.character_card_exporter import CardExportFailure, CardExportReport


def _canonical_character(data) -> str:
    return json.dumps(data, sort_keys=True, ensure_ascii=False, default=str)


def _data_sha(data: dict) -> str:
    return hashlib.sha256(_canonical_character(data).encode("utf-8")).hexdigest()


def _write_card(cards_dir: Path, filename: str, body: bytes) -> Path:
    cards_dir.mkdir(parents=True, exist_ok=True)
    path = cards_dir / filename
    path.write_bytes(body)
    return path


def _report(outputs: dict[str, Path] | None = None, failures: dict[str, str] | None = None) -> CardExportReport:
    outputs = outputs or {}
    failures = failures or {}
    return CardExportReport(
        seen=len(outputs) + len(failures),
        created=len(outputs),
        warned=0,
        failed=len(failures),
        outputs=tuple(outputs.values()),
        warnings=(),
        failures=tuple(CardExportFailure(character_id, error) for character_id, error in failures.items()),
        character_outputs=tuple(outputs.items()),
    )


def _entries_by_id(payload: dict) -> dict[str, dict]:
    return {str(entry["character_id"]): entry for entry in payload["characters"]}


def test_write_card_manifest_publishes_rendered_characters_with_artifact_and_data_sha(tmp_path):
    from server_app.character_card_manifest import (
        SCHEMA_VERSION,
        load_card_manifest,
        write_card_manifest,
    )

    cards_dir = tmp_path / "character_cards"
    card_1 = _write_card(cards_dir, "10001_阿尔法_角色档案_长图.png", b"png-10001-v100")
    card_2 = _write_card(cards_dir, "10002_贝塔_角色档案_长图.png", b"png-10002-v100")
    characters = {
        "10001": {"name": "阿尔法/Alpha", "max_hp": 100},
        "10002": {"name": "贝塔/Beta", "max_hp": 200},
    }

    payload = write_card_manifest(
        cards_dir,
        version=100,
        characters=characters,
        card_report=_report({"10001": card_1, "10002": card_2}),
        previous_manifest=None,
    )

    assert payload["schema_version"] == SCHEMA_VERSION == 1
    assert payload["version"] == "100"
    assert [entry["character_id"] for entry in payload["characters"]] == ["10001", "10002"]
    entries = _entries_by_id(payload)
    for character_id, card in (("10001", card_1), ("10002", card_2)):
        entry = entries[character_id]
        assert entry["state"] == "published"
        assert entry["source_version"] == "100"
        assert entry["error"] is None
        assert entry["artifact_path"] == f"character_cards/{card.name}"
        assert entry["artifact_sha256"] == hashlib.sha256(card.read_bytes()).hexdigest()
        assert entry["data_sha256"] == _data_sha(characters[character_id])
    # 原子落盘并可回读
    on_disk = load_card_manifest(cards_dir)
    assert on_disk == payload


def test_data_sha256_tracks_content_and_recovers_after_revert(tmp_path):
    from server_app.character_card_manifest import write_card_manifest

    cards_dir = tmp_path / "character_cards"
    character_id = "10001"
    data_a = {"name": "阿尔法/Alpha", "description": "第一版"}
    data_b = {"name": "阿尔法/Alpha", "description": "第二版"}

    card_a = _write_card(cards_dir, "card-a.png", b"png-a")
    payload_a = write_card_manifest(
        cards_dir, version=100, characters={character_id: data_a},
        card_report=_report({character_id: card_a}), previous_manifest=None,
    )
    card_b = _write_card(cards_dir, "card-b.png", b"png-b")
    payload_b = write_card_manifest(
        cards_dir, version=101, characters={character_id: data_b},
        card_report=_report({character_id: card_b}), previous_manifest=payload_a,
    )

    sha_a = _entries_by_id(payload_a)[character_id]["data_sha256"]
    sha_b = _entries_by_id(payload_b)[character_id]["data_sha256"]
    assert sha_a != sha_b

    # A→B→A 回退后 data_sha256 恢复为 A 的值：内容修订链按内容而非按版本号识别
    card_a2 = _write_card(cards_dir, "card-a2.png", b"png-a-again")
    payload_a2 = write_card_manifest(
        cards_dir, version=102, characters={character_id: data_a},
        card_report=_report({character_id: card_a2}), previous_manifest=payload_b,
    )
    entry_a2 = _entries_by_id(payload_a2)[character_id]
    assert entry_a2["data_sha256"] == sha_a
    assert entry_a2["source_version"] == "102"


def test_unchanged_characters_inherit_previous_entry_keeping_source_version(tmp_path):
    from server_app.character_card_manifest import write_card_manifest

    cards_dir = tmp_path / "character_cards"
    characters = {"10001": {"name": "阿尔法/Alpha"}, "10002": {"name": "贝塔/Beta"}}
    card_1 = _write_card(cards_dir, "10001_阿尔法_角色档案_长图.png", b"png-10001")
    card_2 = _write_card(cards_dir, "10002_贝塔_角色档案_长图.png", b"png-10002")
    previous = write_card_manifest(
        cards_dir, version=100, characters=characters,
        card_report=_report({"10001": card_1, "10002": card_2}), previous_manifest=None,
    )
    previous_entries = _entries_by_id(previous)

    # 101 版只有 10002 被重渲；10001 未变化、无新产物，必须继承 100 版条目
    card_2_new = _write_card(cards_dir, "10002_贝塔_角色档案_长图.png", b"png-10002-v101")
    payload = write_card_manifest(
        cards_dir, version=101, characters=characters,
        card_report=_report({"10002": card_2_new}), previous_manifest=previous,
    )

    entries = _entries_by_id(payload)
    inherited = entries["10001"]
    assert inherited["state"] == "published"
    assert inherited["source_version"] == "100"
    assert inherited["artifact_path"] == previous_entries["10001"]["artifact_path"]
    assert inherited["artifact_sha256"] == previous_entries["10001"]["artifact_sha256"]
    assert inherited["data_sha256"] == previous_entries["10001"]["data_sha256"]
    # 被重渲的角色以新内容发布
    republished = entries["10002"]
    assert republished["state"] == "published"
    assert republished["source_version"] == "101"
    assert republished["artifact_sha256"] == hashlib.sha256(card_2_new.read_bytes()).hexdigest()


def test_changed_character_with_failed_render_stays_stale_with_old_content(tmp_path):
    from server_app.character_card_manifest import write_card_manifest

    cards_dir = tmp_path / "character_cards"
    old_data = {"name": "阿尔法/Alpha", "description": "旧"}
    card_old = _write_card(cards_dir, "10001_阿尔法_角色档案_长图.png", b"png-old")
    previous = write_card_manifest(
        cards_dir, version=100, characters={"10001": old_data},
        card_report=_report({"10001": card_old}), previous_manifest=None,
    )
    previous_entry = _entries_by_id(previous)["10001"]

    # 101 版角色数据变化、渲染失败：保留旧有效内容并标 stale，不得伪装成新修订
    new_data = {"name": "阿尔法/Alpha", "description": "新"}
    payload = write_card_manifest(
        cards_dir, version=101, characters={"10001": new_data},
        card_report=_report(failures={"10001": "node renderer exited 1"}),
        previous_manifest=previous,
    )

    entry = _entries_by_id(payload)["10001"]
    assert entry["state"] == "stale"
    assert entry["source_version"] == "100"
    assert entry["artifact_path"] == previous_entry["artifact_path"]
    assert entry["artifact_sha256"] == previous_entry["artifact_sha256"]
    # data 哈希维持旧值：查询侧不会把旧图当成 101 的新内容修订
    assert entry["data_sha256"] == previous_entry["data_sha256"]
    assert entry["data_sha256"] != _data_sha(new_data)
    assert "node renderer exited 1" in str(entry["error"])


def test_renamed_character_keeps_single_entry_pointing_at_new_artifact(tmp_path):
    from server_app.character_card_manifest import write_card_manifest

    cards_dir = tmp_path / "character_cards"
    old_card = _write_card(cards_dir, "10001_旧名_角色档案_长图.png", b"png-old-name")
    previous = write_card_manifest(
        cards_dir, version=100, characters={"10001": {"name": "旧名/Old"}},
        card_report=_report({"10001": old_card}), previous_manifest=None,
    )
    # 旧文件仍留在目录中（与线上目录一致，不被清理）
    assert old_card.is_file()

    new_card = _write_card(cards_dir, "10001_新名_角色档案_长图.png", b"png-new-name")
    payload = write_card_manifest(
        cards_dir, version=101, characters={"10001": {"name": "新名/New"}},
        card_report=_report({"10001": new_card}), previous_manifest=previous,
    )

    assert [entry["character_id"] for entry in payload["characters"]] == ["10001"]
    entry = _entries_by_id(payload)["10001"]
    assert entry["state"] == "published"
    assert entry["artifact_path"] == f"character_cards/{new_card.name}"
    assert entry["artifact_sha256"] == hashlib.sha256(new_card.read_bytes()).hexdigest()
    serialized = json.dumps(payload, ensure_ascii=False)
    assert old_card.name not in serialized


def test_deleted_character_drops_out_even_if_legacy_file_remains(tmp_path):
    from server_app.character_card_manifest import write_card_manifest

    cards_dir = tmp_path / "character_cards"
    card_1 = _write_card(cards_dir, "10001_阿尔法_角色档案_长图.png", b"png-10001")
    card_2 = _write_card(cards_dir, "10002_贝塔_角色档案_长图.png", b"png-10002")
    previous = write_card_manifest(
        cards_dir, version=100,
        characters={"10001": {"name": "阿尔法"}, "10002": {"name": "贝塔"}},
        card_report=_report({"10001": card_1, "10002": card_2}), previous_manifest=None,
    )

    # 101 版 10002 已从游戏数据删除；旧 PNG 仍留在目录中也不能重新变成有效条目
    payload = write_card_manifest(
        cards_dir, version=101, characters={"10001": {"name": "阿尔法"}},
        card_report=_report({}), previous_manifest=previous,
    )

    assert [entry["character_id"] for entry in payload["characters"]] == ["10001"]
    assert "10002" not in _entries_by_id(payload)


def test_validate_card_manifest_flags_escape_missing_file_and_sha_mismatch(tmp_path):
    from server_app.character_card_manifest import (
        validate_card_manifest,
        write_card_manifest,
    )

    cards_dir = tmp_path / "character_cards"
    card = _write_card(cards_dir, "10001_阿尔法_角色档案_长图.png", b"png-good")
    payload = write_card_manifest(
        cards_dir, version=100, characters={"10001": {"name": "阿尔法"}},
        card_report=_report({"10001": card}), previous_manifest=None,
    )
    assert validate_card_manifest(payload, cards_dir) == ()

    def _mutated(**changes) -> tuple[str, ...]:
        broken = json.loads(json.dumps(payload))
        entry = broken["characters"][0]
        entry.update(changes)
        return validate_card_manifest(broken, cards_dir)

    assert _mutated(artifact_path="../outside.png")
    assert _mutated(artifact_path=str(card))
    assert _mutated(artifact_path="character_cards/不存在_角色档案_长图.png")
    assert _mutated(artifact_sha256="0" * 64)


def test_load_card_manifest_returns_none_when_missing_or_invalid(tmp_path):
    from server_app.character_card_manifest import load_card_manifest

    assert load_card_manifest(tmp_path / "no-such-dir") is None
    broken = tmp_path / "character_cards"
    broken.mkdir()
    (broken / "manifest.json").write_text("not-json{", encoding="utf-8")
    assert load_card_manifest(broken) is None


# ---- 复审缺口回归 -------------------------------------------------------------


def test_stale_entry_carries_forward_when_render_still_absent(tmp_path):
    from server_app.character_card_manifest import write_card_manifest

    cards_dir = tmp_path / "character_cards"
    old_data = {"name": "阿尔法/Alpha", "description": "旧"}
    old_card = _write_card(cards_dir, "10001_阿尔法_角色档案_长图.png", b"png-old")
    v100 = write_card_manifest(
        cards_dir, version=100, characters={"10001": old_data},
        card_report=_report({"10001": old_card}), previous_manifest=None,
    )

    # 101 版内容变化、渲染失败 → stale
    changed_data = {"name": "阿尔法/Alpha", "description": "新"}
    v101 = write_card_manifest(
        cards_dir, version=101, characters={"10001": changed_data},
        card_report=_report(failures={"10001": "node renderer exited 1"}),
        previous_manifest=v100,
    )
    stale_entry = _entries_by_id(v101)["10001"]
    assert stale_entry["state"] == "stale"

    # 102 版仍未重渲（无输出、无失败记录）：必须继续携带 stale，角色不得丢失
    newer_data = {"name": "阿尔法/Alpha", "description": "又变了"}
    v102 = write_card_manifest(
        cards_dir, version=102, characters={"10001": newer_data},
        card_report=_report(), previous_manifest=v101,
    )
    carried = _entries_by_id(v102)["10001"]
    assert carried["state"] == "stale"
    assert carried["artifact_path"] == stale_entry["artifact_path"]
    assert carried["artifact_sha256"] == stale_entry["artifact_sha256"]
    assert carried["data_sha256"] == stale_entry["data_sha256"]
    assert carried["source_version"] == "100"
    assert "node renderer exited 1" in str(carried["error"])


def _legacy_version_dir(tmp_path: Path, cards: dict[str, bytes], *, outputs: list[str] | None) -> Path:
    version_dir = tmp_path / "versions" / "100"
    cards_dir = version_dir / "character_cards"
    cards_dir.mkdir(parents=True)
    for name, body in cards.items():
        (cards_dir / name).write_bytes(body)
    (version_dir / "character_data").mkdir()
    (version_dir / "character_data" / "current.json").write_text(
        json.dumps({"version": 100, "characters": {"10001": {"name": "阿尔法"}}}, ensure_ascii=False),
        encoding="utf-8",
    )
    root_manifest: dict = {"version": 100}
    if outputs is not None:
        root_manifest["character_cards"] = {"outputs": outputs}
    (version_dir / "manifest.json").write_text(json.dumps(root_manifest, ensure_ascii=False), encoding="utf-8")
    return version_dir


def test_legacy_manifest_prefers_declared_output_when_rename_leaves_multiple_pngs(tmp_path):
    from server_app.character_card_manifest import legacy_manifest_from_version

    version_dir = _legacy_version_dir(
        tmp_path,
        {
            "10001_旧名_角色档案_长图.png": b"png-old-name",
            "10001_新名_角色档案_长图.png": b"png-new-name",
        },
        outputs=["character_cards/10001_新名_角色档案_长图.png"],
    )

    manifest = legacy_manifest_from_version(version_dir, 100)

    (entry,) = manifest["characters"]
    assert entry["character_id"] == "10001"
    assert entry["artifact_path"] == "character_cards/10001_新名_角色档案_长图.png"
    assert entry["artifact_sha256"] == hashlib.sha256(b"png-new-name").hexdigest()


def test_legacy_manifest_skips_ambiguous_candidates_instead_of_guessing(tmp_path):
    from server_app.character_card_manifest import legacy_manifest_from_version

    version_dir = _legacy_version_dir(
        tmp_path,
        {
            "10001_旧名_角色档案_长图.png": b"png-old-name",
            "10001_新名_角色档案_长图.png": b"png-new-name",
        },
        outputs=None,  # 根 manifest 无 outputs 声明 → 歧义
    )

    manifest = legacy_manifest_from_version(version_dir, 100)

    assert manifest is not None
    assert manifest["characters"] == []  # 歧义角色被跳过，绝不按文件名猜
    # 两种 PNG 都保持原样在目录中
    assert (version_dir / "character_cards" / "10001_旧名_角色档案_长图.png").is_file()
    assert (version_dir / "character_cards" / "10001_新名_角色档案_长图.png").is_file()


def test_legacy_manifest_single_candidate_still_matches(tmp_path):
    from server_app.character_card_manifest import legacy_manifest_from_version

    version_dir = _legacy_version_dir(
        tmp_path,
        {"10001_阿尔法_角色档案_长图.png": b"png-single"},
        outputs=None,
    )

    manifest = legacy_manifest_from_version(version_dir, 100)

    (entry,) = manifest["characters"]
    assert entry["artifact_path"] == "character_cards/10001_阿尔法_角色档案_长图.png"
    assert entry["data_sha256"] == _data_sha({"name": "阿尔法"})
