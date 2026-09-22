from server_app.character_card_exporter import CardExportReport
from server_app.processor import build_manifest_payload


def test_manifest_builder_records_card_stage(tmp_path):
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

    assert payload["character_cards"]["created"] == 1
    assert payload["character_cards"]["outputs"] == ["character_cards/10001.png"]
