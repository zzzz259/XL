from app.core import database as db


def test_record_changes_stores_size_delta(tmp_path):
    db.init_db(str(tmp_path / "test.db"))
    db.save_version(
        1,
        {},
        {"data": [{"name": "catalog", "hash": "old", "size": 100, "ver": 1}]},
        is_current=True,
    )
    db.save_version(
        2,
        {},
        {"data": [{"name": "catalog", "hash": "new", "size": 140, "ver": 2}]},
        is_current=False,
    )

    db.record_changes(1, 2)

    changes = db.get_version_changes(1, 2)
    assert changes[0][-1] == 40
