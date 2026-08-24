from datetime import datetime

from app.platform import database as db
from app.features.versions.version_update import append_changelog, record_downloaded_bundle


def test_append_changelog_normalizes_multiline_message(tmp_path):
    output = tmp_path / "output" / "CHANGELOG.md"
    append_changelog(str(output), "完成第一步\n完成第二步", datetime(2026, 8, 17, 12, 34, 56))

    assert output.read_text(encoding="utf-8") == "- 2026-08-17 12:34:56 | 完成第一步 完成第二步\n"


def test_record_downloaded_bundle_updates_database(tmp_path):
    db.init_db(str(tmp_path / "test.db"))
    db.save_version(100, {}, {"data": []})
    db.save_sub_bundles(100, ["known"])

    record_downloaded_bundle(100, "known", str(tmp_path / "known.bundle"))

    assert db.get_sub_bundles(100)[0][2] == str(tmp_path / "known.bundle")
