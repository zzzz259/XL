import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

from xl_deploy.poller import BranchPoller, PollCursorStore

OLD_SHA = "1" * 40
NEW_SHA = "2" * 40


class _GitHub:
    def latest_sha(self, branch):
        return NEW_SHA

    def ci_result(self, branch, sha):
        return "success"


class _Runner:
    def changed_paths(self, base_sha, head_sha):
        return ("xl_qqbot/bot_app/router.py",)

    def read_text_at_commit(self, commit_sha, path):
        return ""


class _Transaction:
    def __init__(self, current_release):
        self.paths = SimpleNamespace(current_path=Path("/deploy/current"))
        self.state = SimpleNamespace(
            get_current=lambda *, current_path: current_release
        )

    def recover(self):
        return SimpleNamespace(status="nothing_to_recover", sha=None, message="")

    def execute(self, plan):
        return SimpleNamespace(status="success", sha=plan.sha, message="")


class PollerResultStatusTests(unittest.TestCase):
    def test_success_result_advances_branch_cursor(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            current_release = root / f"{OLD_SHA}-previous"
            current_release.mkdir()
            cursors = PollCursorStore(root / "poll-cursors.json")
            cursors.set_cursor("test", OLD_SHA)
            transaction = _Transaction(current_release)
            poller = BranchPoller(
                _GitHub(),
                _Runner(),
                lambda branch: transaction,
                cursors,
            )

            result = poller.poll_branch("test")

            self.assertEqual(result.status, "deployed")
            self.assertEqual(cursors.get_cursor("test"), NEW_SHA)


if __name__ == "__main__":
    unittest.main()
