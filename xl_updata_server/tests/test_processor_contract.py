from pathlib import Path

from server_app.character_card_exporter import CardExportReport
from server_app.processor import build_manifest_payload, bundle_output_dir


def test_bundle_output_dir_is_version_local(tmp_path):
    version_root = tmp_path / "versions" / "123"

    assert bundle_output_dir(version_root, "Data") == version_root / "bundles" / "Data"


def test_manifest_builder_records_lua_only_stage(tmp_path):
    report = CardExportReport(
        seen=1,
        created=1,
        warned=0,
        failed=0,
        outputs=(tmp_path / "character_cards" / "10001.png",),
        warnings=(),
        failures=(),
    )

    payload = build_manifest_payload(
        version=123,
        lua_hashes=("lua",),
        character_ids=("10001",),
        new_character_ids={"10001"},
        card_report=report,
        staging=tmp_path,
    )

    assert payload["version"] == 123
    assert payload["baseline"] is False
    assert payload["lua_hashes"] == ["lua"]
    assert payload["new_characters_count"] == 1
    assert payload["character_cards"]["created"] == 1
    assert payload["character_cards"]["outputs"] == ["character_cards/10001.png"]
    assert "portrait_hashes" not in payload
    assert "skins" not in payload
    assert payload["downloaded_hashes"] == ["lua"]


def test_manifest_builder_marks_baseline_run(tmp_path):
    report = CardExportReport(
        seen=100,
        created=100,
        warned=0,
        failed=0,
        outputs=(),
        warnings=(),
        failures=(),
    )

    payload = build_manifest_payload(
        version=1,
        lua_hashes=("lua",),
        character_ids=tuple(str(i) for i in range(100)),
        new_character_ids=set(str(i) for i in range(100)),
        card_report=report,
        staging=tmp_path,
        baseline=True,
    )

    assert payload["baseline"] is True
    assert payload["new_characters_count"] == 100
