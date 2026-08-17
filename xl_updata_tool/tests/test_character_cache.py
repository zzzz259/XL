from app.core.character_cache import derive_character_index, load_cache, save_cache, source_mtime


def test_derive_character_index_filters_and_sorts_roles():
    data = {
        "second": {"raw_id": 80100002, "name": "乙/extra"},
        "outside": {"raw_id": 80110000, "name": "skip"},
        "first": {"raw_id": 80100001, "name": "甲"},
    }

    assert derive_character_index(data) == [
        {"name": "甲", "char_id": "first", "raw_id": 80100001, "display_index": 80100001},
        {"name": "乙", "char_id": "second", "raw_id": 80100002, "display_index": 80100002},
    ]


def test_character_cache_round_trip_and_invalidation(tmp_path):
    source = tmp_path / "BaseCard.lua"
    source.write_text("return {}", encoding="utf-8")
    timestamp = source_mtime(str(tmp_path), [source.name])
    cache_path = tmp_path / "cache" / "characters.json"
    data = {"80100001": {"raw_id": 80100001}}

    save_cache(str(cache_path), data, timestamp)

    assert load_cache(str(cache_path), timestamp) == data
    assert load_cache(str(cache_path), timestamp + 1) is None
