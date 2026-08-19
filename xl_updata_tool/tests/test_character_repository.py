from app.core.character_repository import (
    clear_all_unread,
    clear_unread,
    current_characters,
    load_repository,
    merge_snapshot,
    repository_path,
    unread_status,
)


def test_merge_snapshot_keeps_history_and_tracks_new_or_changed_roles(tmp_path):
    data_dir = tmp_path / "character_data"
    first = {
        80100001: {"name": "甲", "attack": 100},
        80100002: {"name": "乙", "attack": 200},
    }
    first_result = merge_snapshot(data_dir, 100, first, source_dir=tmp_path / "lua" / "100")

    assert first_result["changes"] == {"80100001": "new", "80100002": "new"}
    assert current_characters(load_repository(repository_path(data_dir))) == first
    assert (data_dir / "versions" / "100.json").is_file()

    assert clear_unread(data_dir, 80100001) is True
    second = {
        80100001: {"name": "甲", "attack": 125},
        80100002: {"name": "乙", "attack": 200},
        80100003: {"name": "丙", "attack": 300},
    }
    second_result = merge_snapshot(data_dir, 200, second, source_dir=tmp_path / "lua" / "200")
    repository = load_repository(repository_path(data_dir))

    assert second_result["changes"] == {
        "80100001": "changed",
        "80100003": "new",
    }
    assert unread_status(repository) == {
        "80100002": "new",
        "80100001": "changed",
        "80100003": "new",
    }
    assert repository["current_version"] == 200
    assert set(repository["history"]) == {"100", "200"}
    assert current_characters(repository) == second


def test_clear_unread_is_idempotent(tmp_path):
    data_dir = tmp_path / "character_data"
    merge_snapshot(data_dir, 100, {80100001: {"name": "甲"}})

    assert clear_unread(data_dir, "80100001") is True
    assert clear_unread(data_dir, 80100001) is False
    assert unread_status(load_repository(repository_path(data_dir))) == {}


def test_legacy_baseline_prevents_first_migration_from_marking_everything_new(tmp_path):
    data_dir = tmp_path / "character_data"
    baseline = {80100001: {"name": "甲", "attack": 100}}
    current = {80100001: {"name": "甲", "attack": 100}, 80100002: {"name": "乙"}}

    result = merge_snapshot(data_dir, 200, current, baseline_characters=baseline)

    assert result["changes"] == {"80100002": "new"}
    assert unread_status(load_repository(repository_path(data_dir))) == {"80100002": "new"}


def test_clear_all_unread_is_idempotent(tmp_path):
    data_dir = tmp_path / "character_data"
    merge_snapshot(data_dir, 100, {80100001: {"name": "甲"}, 80100002: {"name": "乙"}})

    assert clear_all_unread(data_dir) == 2
    assert clear_all_unread(data_dir) == 0
    assert unread_status(load_repository(repository_path(data_dir))) == {}
