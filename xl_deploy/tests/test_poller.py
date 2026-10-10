from pathlib import Path

import pytest

from xl_deploy.poller import BranchPoller, PollCursorStore
from xl_deploy.runner import CommandRunner

OLD_SHA = "1" * 40
NEW_SHA = "2" * 40


class FakeGitHub:
    def __init__(self, *, latest=NEW_SHA, ci="success"):
        self.latest = latest
        self.ci = ci
        self.ci_calls = []

    def latest_sha(self, branch):
        return self.latest

    def ci_result(self, branch, sha):
        self.ci_calls.append((branch, sha))
        return self.ci


class FakeRunner:
    def __init__(self, paths, announcement_text=None):
        self.paths = paths
        self.diff_calls = []
        self.announcement_text = announcement_text or {}
        self.text_calls = []

    def changed_paths(self, base, head):
        self.diff_calls.append((base, head))
        return self.paths

    def read_text_at_commit(self, commit_sha, path):
        self.text_calls.append((commit_sha, path))
        return self.announcement_text[path]


class FakeState:
    def __init__(self, current, journal=None):
        self.current = current
        self.journal = journal

    def get_current(self, *, current_path):
        assert current_path.name in {"debug", "test", "main"}
        return self.current

    def load(self):
        return self.journal


class FakeTransaction:
    def __init__(self, current, status="completed", recovery_status="nothing_to_recover", journal=None):
        self.paths = type("Paths", (), {"current_path": Path("/deploy/current")})()
        self.state = FakeState(current, journal)
        self.status = status
        self.recovery_status = recovery_status
        self.calls = []

    def recover(self):
        self.calls.append(("recover",))
        return type("Result", (), {"status": self.recovery_status, "sha": NEW_SHA, "message": ""})()

    def execute(self, plan):
        self.calls.append(("execute", plan))
        return type("Result", (), {"status": self.status, "sha": plan.sha, "message": ""})()


class FakeReleaseGitHub(FakeGitHub):
    def release_notes(self):
        return [{
            "tag_name": "v2.0.0",
            "target_sha": NEW_SHA,
            "body": "Release highlights",
            "published_at": "2026-09-24T00:00:00Z",
        }]


def make_poller(
    tmp_path, *, paths, ci="success", status="completed", cursor=OLD_SHA,
    current=None, branch="debug",
):
    current = current or tmp_path / f"{OLD_SHA}-previous"
    current.mkdir(exist_ok=True)
    transaction = FakeTransaction(current, status)
    store = PollCursorStore(tmp_path / "poll-state.json")
    if cursor is not None:
        store.set_cursor(branch, cursor)
    github = FakeGitHub(ci=ci)
    runner = FakeRunner(paths)
    poller = BranchPoller(
        github,
        runner,
        lambda _branch: transaction,
        store,
    )
    return poller, github, runner, transaction, store


def test_poll_cursor_is_persistent_and_independent_per_branch(tmp_path):
    store = PollCursorStore(tmp_path / "poll-state.json")

    store.set_cursor("debug", NEW_SHA)

    reloaded = PollCursorStore(tmp_path / "poll-state.json")
    assert reloaded.get_cursor("debug") == NEW_SHA
    assert reloaded.get_cursor("test") is None


def test_pending_ci_does_not_diff_deploy_or_advance_cursor(tmp_path):
    poller, github, runner, transaction, store = make_poller(
        tmp_path, paths=["xl_qqbot/bot_app/router.py"], ci="pending"
    )

    result = poller.poll_branch("debug")

    assert result.status == "ci_pending"
    assert runner.diff_calls == []
    assert transaction.calls == [("recover",)]
    assert store.get_cursor("debug") == OLD_SHA
    assert github.ci_calls == [("debug", NEW_SHA)]


def test_ci_failure_does_not_advance_cursor(tmp_path):
    poller, _, runner, _, store = make_poller(
        tmp_path, paths=["xl_qqbot/bot_app/router.py"], ci="failure"
    )

    result = poller.poll_branch("debug")

    assert result.status == "ci_failed"
    assert runner.diff_calls == []
    assert store.get_cursor("debug") == OLD_SHA


def test_noop_commit_advances_cursor_after_successful_ci(tmp_path):
    poller, _, runner, transaction, store = make_poller(tmp_path, paths=["README.md"])

    result = poller.poll_branch("debug")

    assert result.status == "no_op"
    assert runner.diff_calls == [(OLD_SHA, NEW_SHA)]
    assert transaction.calls == [("recover",)]
    assert store.get_cursor("debug") == NEW_SHA


def test_deployment_failure_keeps_previous_cursor(tmp_path):
    poller, _, _, _, store = make_poller(
        tmp_path, paths=["xl_qqbot/bot_app/router.py"], status="rolled_back",
        branch="test",
    )

    result = poller.poll_branch("test")

    assert result.status == "deployment_rolled_back"
    assert store.get_cursor("test") == OLD_SHA


def test_successful_deployment_advances_only_that_branch_cursor(tmp_path):
    poller, _, _, transaction, store = make_poller(
        tmp_path, paths=["xl_qqbot/bot_app/router.py"], branch="test"
    )

    result = poller.poll_branch("test")

    assert result.status == "deployed"
    assert transaction.calls[1][0] == "execute"
    assert store.get_cursor("test") == NEW_SHA
    assert store.get_cursor("debug") is None


def test_missing_current_release_fails_closed_and_does_not_advance_cursor(tmp_path):
    poller, _, runner, _, store = make_poller(
        tmp_path, paths=["xl_qqbot/bot_app/router.py"], cursor=None, current=None,
        branch="test",
    )
    transaction = FakeTransaction(None)
    poller.transaction_for_branch = lambda _branch: transaction

    with pytest.raises(RuntimeError, match="bootstrap"):
        poller.poll_branch("test")

    assert runner.diff_calls == []
    assert store.get_cursor("test") is None


def test_command_runner_fetches_and_diffs_exact_validated_sha_range(tmp_path):
    commands = []

    def executor(args, **kwargs):
        commands.append((args, kwargs))
        return type("Result", (), {"stdout": "xl_qqbot/bot_app/router.py\0"})()

    runner = CommandRunner(repository=tmp_path, executor=executor)

    assert runner.changed_paths(OLD_SHA, NEW_SHA) == ("xl_qqbot/bot_app/router.py",)
    assert commands[0][0] == ["git", "fetch", "--no-tags", "origin", OLD_SHA, NEW_SHA]
    assert commands[1][0] == ["git", "diff", "--name-only", "-z", "--no-renames", f"{OLD_SHA}..{NEW_SHA}"]


def test_command_runner_rejects_branch_text_in_sha_diff_args(tmp_path):
    runner = CommandRunner(repository=tmp_path, executor=lambda *_args, **_kwargs: None)

    with pytest.raises(ValueError, match="SHA"):
        runner.changed_paths("main", NEW_SHA)


def test_command_runner_preserves_newlines_inside_git_path_names(tmp_path):
    def executor(args, **kwargs):
        return type("Result", (), {"stdout": "xl_qqbot/strange\nname.py\0"})()

    runner = CommandRunner(repository=tmp_path, executor=executor)

    assert runner.changed_paths(OLD_SHA, NEW_SHA) == ("xl_qqbot/strange\nname.py",)


@pytest.mark.parametrize("branch", ["test", "main"])
def test_project_announcement_is_attached_for_each_branch(tmp_path, branch):
    current = tmp_path / f"{OLD_SHA}-previous"
    current.mkdir()
    transaction = FakeTransaction(current)
    store = PollCursorStore(tmp_path / "poll-state.json")
    store.set_cursor(branch, OLD_SHA)
    note_path = "xl_deploy/announcements/test-main-isolated-update.md"
    poller = BranchPoller(
        FakeGitHub(),
        FakeRunner(
            ["xl_qqbot/bot_app/router.py", note_path],
            {note_path: "Isolated test update"},
        ),
        lambda _branch: transaction,
        store,
    )

    result = poller.poll_branch(branch)

    assert result.status == "deployed"
    assert transaction.calls[1][1].release_note == "Isolated test update"
    assert store.recorded_releases() == (set(), set())


def test_missing_project_announcement_does_not_block_lifecycle_deployment(tmp_path):
    current = tmp_path / f"{OLD_SHA}-previous"
    current.mkdir()
    transaction = FakeTransaction(current, status="announcement_pending")
    store = PollCursorStore(tmp_path / "poll-state.json")
    store.set_cursor("test", OLD_SHA)
    poller = BranchPoller(
        FakeGitHub(),
        FakeRunner(["xl_qqbot/bot_app/router.py"]),
        lambda _branch: transaction,
        store,
    )

    result = poller.poll_branch("test")

    assert result.status == "deployment_announcement_pending"
    assert store.get_cursor("test") == OLD_SHA
    assert store.recorded_releases() == (set(), set())


def test_reconciles_committed_pointer_after_crash_before_cursor_write(tmp_path):
    current = tmp_path / f"{NEW_SHA}-already-committed"
    current.mkdir()
    transaction = FakeTransaction(current, recovery_status="completed")
    store = PollCursorStore(tmp_path / "poll-state.json")
    store.set_cursor("debug", OLD_SHA)
    runner = FakeRunner(["xl_qqbot/bot_app/router.py"])
    poller = BranchPoller(
        FakeGitHub(), runner, lambda _branch: transaction, store
    )

    result = poller.poll_branch("debug")

    assert result.status == "up_to_date"
    assert runner.diff_calls == []
    assert transaction.calls == [("recover",)]
    assert store.get_cursor("debug") == NEW_SHA


def test_recovery_keeps_legacy_journal_compatible_without_release_lookup(tmp_path):
    current = tmp_path / f"{NEW_SHA}-already-committed"
    current.mkdir()
    journal = type("Journal", (), {
        "branch": "main", "sha": NEW_SHA, "phase": "completed",
        "release_note": "Release highlights",
    })()
    transaction = FakeTransaction(current, recovery_status="already_recovered", journal=journal)
    store = PollCursorStore(tmp_path / "poll-state.json")
    store.set_cursor("main", OLD_SHA)
    runner = FakeRunner(["xl_qqbot/bot_app/router.py"])
    poller = BranchPoller(
        FakeGitHub(), runner, lambda _branch: transaction, store
    )

    result = poller.poll_branch("main")

    assert result.status == "up_to_date"
    assert runner.diff_calls == []
    assert store.get_cursor("main") == NEW_SHA
    assert store.recorded_releases() == (set(), set())


def test_active_remote_tip_does_not_advance_cursor_until_exact_sha_ci_succeeds(tmp_path):
    current = tmp_path / f"{NEW_SHA}-already-active"
    current.mkdir()
    transaction = FakeTransaction(current)
    store = PollCursorStore(tmp_path / "poll-state.json")
    store.set_cursor("debug", OLD_SHA)
    github = FakeGitHub(latest=NEW_SHA, ci="pending")
    runner = FakeRunner([])
    poller = BranchPoller(github, runner, lambda _branch: transaction, store)

    result = poller.poll_branch("debug")

    assert result.status == "ci_pending"
    assert github.ci_calls == [("debug", NEW_SHA)]
    assert runner.diff_calls == []
    assert store.get_cursor("debug") == OLD_SHA
