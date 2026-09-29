from xl_deploy.announcements import ReleaseNoteSelector


def test_selector_emits_only_new_nonempty_notes_in_newest_first_order():
    selector = ReleaseNoteSelector()
    entries = [
        {"tag_name": "v1", "sha": "a1", "body": "older notes", "published_at": "2026-01-01"},
        {"tag_name": "v2", "sha": "b2", "body": "new release", "published_at": "2026-02-01"},
        {"tag_name": "v3", "sha": "c3", "body": "  ", "published_at": "2026-03-01"},
    ]

    selected = selector.select(entries)

    assert selected.tag == "v2"
    assert selected.sha == "b2"
    assert selected.content == "new release"


def test_selector_deduplicates_by_recorded_tag_or_sha():
    selector = ReleaseNoteSelector()
    entries = [
        {"tag_name": "v1", "sha": "already-seen", "body": "duplicate by sha"},
        {"tag_name": "already-seen-tag", "sha": "s2", "body": "duplicate by tag"},
        {"tag_name": "v3", "sha": "s3", "body": "new notes"},
    ]

    selected = selector.select(
        entries,
        recorded_tags={"already-seen-tag"},
        recorded_shas={"already-seen"},
    )

    assert selected.tag == "v3"


def test_selector_returns_none_when_every_note_is_empty_or_seen():
    selector = ReleaseNoteSelector()

    assert selector.select([{"tag_name": "v1", "sha": "s1", "body": " "}]) is None
    assert selector.select([{"tag_name": "v2", "sha": "s2", "body": None}]) is None
    assert selector.select([{"tag_name": None, "sha": None, "body": "notes"}]) is None
    assert selector.select(
        [{"tag_name": "v1", "sha": "s1", "body": "notes"}],
        recorded_tags={"v1"},
    ) is None
