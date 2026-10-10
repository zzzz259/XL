import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

from xl_deploy.poller import BranchPoller, PollCursorStore
from xl_deploy.transaction import DeploymentResult


class TestDebugNoRuntime(unittest.TestCase):
    def test_debug_without_current_release_waits_for_exact_sha_ci_then_records_noop(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            sha = "a" * 40

            class FakeState:
                def get_current(self, *, current_path):
                    return None

            class FakeTransaction:
                paths = SimpleNamespace(current_path=root / "current")
                state = FakeState()

                def recover(self):
                    return DeploymentResult("nothing_to_recover")

            class FakeGithub:
                def __init__(self):
                    self.ci_calls = []

                def latest_sha(self, branch):
                    self.branch = branch
                    return sha

                def ci_result(self, branch, checked_sha):
                    self.ci_calls.append((branch, checked_sha))
                    return "success"

            github = FakeGithub()
            runner = SimpleNamespace(changed_paths=lambda *_args: self.fail("debug must not diff or deploy"))
            poller = BranchPoller(
                github,
                runner,
                lambda _branch: FakeTransaction(),
                PollCursorStore(root / "cursors.json"),
            )

            result = poller.poll_branch("debug")

            self.assertEqual(result.status, "no_op")
            self.assertEqual(github.ci_calls, [("debug", sha)])
            self.assertEqual(poller.cursors.get_cursor("debug"), sha)


if __name__ == "__main__":
    unittest.main()
