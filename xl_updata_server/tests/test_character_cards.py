from server_app.character_cards import (
    character_card_records,
    normalize_character_record,
)


def test_normalize_character_record_keeps_html_fields_and_defaults_missing_values():
    record = normalize_character_record("10001", {"name": "阿尔法/Alpha", "star": "5"})

    assert record.character_id == "10001"
    assert record.data["name"] == "阿尔法/Alpha"
    assert record.data["max_hp"] == 0
    assert record.data["breakthrough_costs"] == []


def test_character_card_records_are_sorted_by_numeric_character_id():
    records = character_card_records({"10002": {"name": "B"}, "10001": {"name": "A"}})

    assert [record.character_id for record in records] == ["10001", "10002"]
