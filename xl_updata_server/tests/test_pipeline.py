"""一次更新任务的 staging、处理、原子发布与图鉴继承边界。"""

from __future__ import annotations

import hashlib
import json

from server_app.pipeline import ProcessResult, UpdatePipeline
from server_app.versioning import read_current_pointer


def _data_sha256(data: dict) -> str:
    canonical = json.dumps(data, sort_keys=True, ensure_ascii=False, default=str)
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def test_pipeline_does_not_replace_current_output_when_processing_fails(tmp_path):
    current = tmp_path / "character_data" / "current.json"
    current.parent.mkdir()
    current.write_text('{"version": 1}', encoding="utf-8")
    def failing_processor(_staging_dir):
        raise RuntimeError("extractor failed")

    pipeline = UpdatePipeline(tmp_path, processor=failing_processor)
    result = pipeline.run_once()
    assert result.processed is False
    assert current.read_text(encoding="utf-8") == '{"version": 1}'


def test_pipeline_converts_unexpected_runtime_failure_to_result(tmp_path):
    def failing_processor(_staging_dir):
        raise ModuleNotFoundError("optional runtime dependency")

    result = UpdatePipeline(tmp_path, processor=failing_processor).run_once()

    assert result.processed is False
    assert result.error == "optional runtime dependency"


def test_pipeline_publishes_successful_result_into_version_directory(tmp_path):
    def successful_processor(staging_dir):
        (staging_dir / "manifest.json").write_text('{"version": 123}', encoding="utf-8")
        return ProcessResult(version_timestamp=123, processed=True)

    result = UpdatePipeline(tmp_path, processor=successful_processor).run_once()

    assert result.processed is True
    assert (tmp_path / "versions" / "123" / "manifest.json").is_file()
    assert read_current_pointer(tmp_path) == 123


def test_process_result_exposes_skin_and_card_counts():
    result = ProcessResult(skin_count=3, card_count=2, card_warning_count=1, card_failure_count=0)

    assert result.skin_count == 3
    assert result.card_count == 2
    assert result.card_warning_count == 1
    assert result.card_failure_count == 0


def test_pipeline_mirrors_character_data_to_data_root(tmp_path):
    def successful_processor(staging_dir):
        current = staging_dir / "character_data" / "current.json"
        current.parent.mkdir(parents=True, exist_ok=True)
        current.write_text('{"version": 123, "characters": {"1": {}}}', encoding="utf-8")
        return ProcessResult(version_timestamp=123, processed=True)

    result = UpdatePipeline(tmp_path, processor=successful_processor).run_once()

    assert result.processed is True
    mirror = tmp_path / "character_data" / "current.json"
    assert mirror.is_file()
    assert '"characters"' in mirror.read_text(encoding="utf-8")


def test_pipeline_publishes_portrait_manifest_and_artifacts(tmp_path):
    def processor_with_portraits(staging_dir):
        portraits = staging_dir / "portraits"
        portraits.mkdir(parents=True)
        (portraits / "10001").mkdir()
        (portraits / "10001" / "1000101.png").write_bytes(b"png")
        (portraits / "manifest.json").write_text(
            '{"schema_version": 1, "version": "123", "skins": []}', encoding="utf-8"
        )
        return ProcessResult(
            version_timestamp=123,
            processed=True,
            portrait_success_count=1,
            portrait_failure_count=0,
            portrait_skip_count=2,
        )

    result = UpdatePipeline(tmp_path, processor=processor_with_portraits).run_once()

    assert result.processed is True
    published = tmp_path / "versions" / "123" / "portraits"
    assert (published / "manifest.json").is_file()
    assert (published / "10001" / "1000101.png").is_file()
    assert result.portrait_success_count == 1
    assert result.portrait_skip_count == 2


def _processor_with_cards(staging_dir, *, characters, cards: dict[str, bytes], version: int = 101):
    """写角色数据与图鉴长图的 stub processor（不触碰 card manifest 实现）。"""
    cards_dir = staging_dir / "character_cards"
    cards_dir.mkdir(parents=True, exist_ok=True)
    for filename, body in cards.items():
        (cards_dir / filename).write_bytes(body)
    current = staging_dir / "character_data" / "current.json"
    current.parent.mkdir(parents=True, exist_ok=True)
    current.write_text(
        json.dumps({"version": version, "characters": characters}, ensure_ascii=False),
        encoding="utf-8",
    )
    (staging_dir / "manifest.json").write_text(json.dumps({"version": version}), encoding="utf-8")
    return ProcessResult(version_timestamp=version, processed=True, schedule_enabled=False)


def test_pipeline_publishes_card_manifest_with_content_hashes(tmp_path):
    cards = {"10001_阿尔法_角色档案_长图.png": b"png-v101"}
    character_data = {"name": "阿尔法/Alpha", "max_hp": 100}

    result = UpdatePipeline(
        tmp_path,
        processor=lambda staging: _processor_with_cards(
            staging, characters={"10001": character_data}, cards=cards
        ),
    ).run_once()

    assert result.processed is True
    manifest_path = tmp_path / "versions" / "101" / "character_cards" / "manifest.json"
    assert manifest_path.is_file()
    payload = json.loads(manifest_path.read_text(encoding="utf-8"))
    assert payload["schema_version"] == 1
    assert payload["version"] == "101"
    (entry,) = payload["characters"]
    assert entry["character_id"] == "10001"
    assert entry["state"] == "published"
    assert entry["source_version"] == "101"
    assert entry["artifact_path"] == "character_cards/10001_阿尔法_角色档案_长图.png"
    assert entry["artifact_sha256"] == hashlib.sha256(b"png-v101").hexdigest()
    assert entry["data_sha256"] == _data_sha256(character_data)


def test_carry_forward_cards_dedupes_renamed_character_by_character_id(tmp_path):
    previous_cards = tmp_path / "versions" / "100" / "character_cards"
    previous_cards.mkdir(parents=True)
    (previous_cards / "10001_旧名_角色档案_长图.png").write_bytes(b"png-old-name")

    result = UpdatePipeline(
        tmp_path,
        processor=lambda staging: _processor_with_cards(
            staging,
            characters={"10001": {"name": "新名/New"}},
            cards={"10001_新名_角色档案_长图.png": b"png-new-name"},
        ),
    ).run_once()

    assert result.processed is True
    published_names = sorted(
        path.name for path in (tmp_path / "versions" / "101" / "character_cards").glob("*.png")
    )
    # 改名后的同角色只允许存在新图；按完整文件名继承会把旧图一并带入
    assert published_names == ["10001_新名_角色档案_长图.png"]


def test_carry_forward_cards_does_not_resurrect_deleted_character(tmp_path):
    previous_cards = tmp_path / "versions" / "100" / "character_cards"
    previous_cards.mkdir(parents=True)
    (previous_cards / "10001_阿尔法_角色档案_长图.png").write_bytes(b"png-10001")
    (previous_cards / "10002_贝塔_角色档案_长图.png").write_bytes(b"png-10002")

    result = UpdatePipeline(
        tmp_path,
        processor=lambda staging: _processor_with_cards(
            staging,
            characters={"10001": {"name": "阿尔法"}},
            cards={"10001_阿尔法_角色档案_长图.png": b"png-10001"},
        ),
    ).run_once()

    assert result.processed is True
    published_names = sorted(
        path.name for path in (tmp_path / "versions" / "101" / "character_cards").glob("*.png")
    )
    # 10002 在 101 版已删除：旧文件仍在 100 版目录中，但不得因此重新变成有效图
    assert published_names == ["10001_阿尔法_角色档案_长图.png"]
