from xl_deploy.announcements import AnnouncementSelector, ReleaseNoteSelector


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


def test_project_announcement_selector_ignores_empty_and_joins_in_path_order():
    texts = {
        "xl_deploy/announcements/z.md": "second",
        "xl_deploy/announcements/a.md": " first ",
        "xl_deploy/announcements/empty.md": "  \n",
    }
    calls = []

    def read_text(commit_sha, path):
        calls.append((commit_sha, path))
        return texts[path]

    selected = AnnouncementSelector().select(
        [*texts], read_text=read_text, commit_sha="a" * 40
    )

    assert selected == "first\n\nsecond"
    assert calls == [
        ("a" * 40, "xl_deploy/announcements/a.md"),
        ("a" * 40, "xl_deploy/announcements/empty.md"),
        ("a" * 40, "xl_deploy/announcements/z.md"),
    ]


def test_project_announcement_selector_returns_none_for_absent_empty_or_oversized():
    selector = AnnouncementSelector()
    reader = lambda _sha, _path: "x" * 4001

    assert selector.select([], read_text=reader, commit_sha="a" * 40) is None
    assert selector.select(
        ["xl_deploy/announcements/a.md"],
        read_text=lambda _sha, _path: "  ",
        commit_sha="a" * 40,
    ) is None
    assert selector.select(
        ["xl_deploy/announcements/a.md"],
        read_text=reader,
        commit_sha="a" * 40,
    ) is None


def test_project_announcement_selector_rejects_traversal_and_non_markdown_paths():
    selector = AnnouncementSelector()

    for path in (
        "xl_deploy/announcements/../secrets.md",
        "xl_deploy/announcements/nested/note.md",
        "xl_deploy/announcements/note.txt",
        "/xl_deploy/announcements/note.md",
    ):
        try:
            selector.select([path], read_text=lambda *_: "note", commit_sha="a" * 40)
        except ValueError:
            pass
        else:
            raise AssertionError(f"unsafe announcement path was accepted: {path}")


def test_command_runner_reads_only_validated_project_announcement_blob(tmp_path):
    from xl_deploy.runner import CommandRunner

    commands = []

    def executor(args, **kwargs):
        commands.append((args, kwargs))
        return type("Result", (), {"stdout": "Release note\n"})()

    runner = CommandRunner(repository=tmp_path, executor=executor)
    assert runner.read_text_at_commit(
        "a" * 40, "xl_deploy/announcements/release.md"
    ) == "Release note\n"
    assert commands[0][0] == [
        "git", "show", f"{'a' * 40}:xl_deploy/announcements/release.md"
    ]

    for sha, path in (
        ("main", "xl_deploy/announcements/release.md"),
        ("a" * 40, "../secrets.env"),
        ("a" * 40, "/xl_deploy/announcements/release.md"),
        ("a" * 40, "xl_deploy/announcements/nested/release.md"),
    ):
        try:
            runner.read_text_at_commit(sha, path)
        except ValueError:
            pass
        else:
            raise AssertionError(f"unsafe blob request was accepted: {sha}:{path}")
