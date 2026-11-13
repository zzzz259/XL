import unittest

from xl_deploy.branch_policy import is_allowed_pull_request


class PullRequestFlowTests(unittest.TestCase):
    def test_task_branch_can_target_debug(self):
        self.assertTrue(is_allowed_pull_request("codex/task", "debug"))

    def test_only_debug_can_target_test(self):
        self.assertTrue(is_allowed_pull_request("debug", "test"))
        self.assertFalse(is_allowed_pull_request("codex/task", "test"))

    def test_only_test_can_target_main(self):
        self.assertTrue(is_allowed_pull_request("test", "main"))
        self.assertFalse(is_allowed_pull_request("codex/task", "main"))

    def test_long_lived_branches_cannot_bypass_debug(self):
        for head in ("debug", "test", "main"):
            with self.subTest(head=head):
                self.assertFalse(is_allowed_pull_request(head, "debug"))

    def test_unknown_target_is_rejected(self):
        self.assertFalse(is_allowed_pull_request("codex/task", "release"))

    def test_missing_refs_are_rejected(self):
        self.assertFalse(is_allowed_pull_request("", "debug"))
        self.assertFalse(is_allowed_pull_request("debug", ""))


if __name__ == "__main__":
    unittest.main()
