import unittest

from xl_deploy.planner import plan_deployment


class TestMainServerMappingTests(unittest.TestCase):
    def test_debug_branch_never_deploys_runtime_units(self):
        for path in (
            "xl_qqbot/bot_app/service.py",
            "xl_updata_server/run_server.py",
        ):
            with self.subTest(path=path):
                plan = plan_deployment("debug", "a" * 40, [path])
                self.assertEqual(plan.impacted_units, ())
                self.assertEqual(plan.impacted_tiers, ())
                self.assertEqual(plan.announcement_scope, ())

    def test_test_bot_change_only_deploys_test_and_drains_both_lower_tiers(self):
        plan = plan_deployment(
            "test", "b" * 40, ["xl_qqbot/bot_app/service.py"]
        )

        self.assertEqual(plan.impacted_units, ("xl-qqbot-test.service",))
        self.assertEqual(plan.impacted_tiers, ("debug", "test"))
        self.assertEqual(plan.announcement_scope, ("debug", "test"))

    def test_test_backend_change_only_deploys_test_server_without_bot_notice(self):
        plan = plan_deployment(
            "test", "c" * 40, ["xl_updata_server/run_server.py"]
        )

        self.assertEqual(plan.impacted_units, ("xl-updata-server-test.service",))
        self.assertEqual(plan.impacted_tiers, ("debug", "test"))
        self.assertEqual(plan.announcement_scope, ("debug", "test"))

    def test_combined_test_bot_and_backend_change_deploys_both_isolated_units(self):
        plan = plan_deployment(
            "test",
            "c" * 40,
            ["xl_qqbot/bot_app/service.py", "xl_updata_server/run_server.py"],
        )

        self.assertEqual(
            plan.impacted_units,
            ("xl-qqbot-test.service", "xl-updata-server-test.service"),
        )
        self.assertEqual(plan.impacted_tiers, ("debug", "test"))
        self.assertEqual(plan.announcement_scope, ("debug", "test"))

    def test_main_bot_change_owns_shared_router_but_not_test_services(self):
        plan = plan_deployment("main", "d" * 40, ["xl_qqbot/bot_app/router.py"])

        self.assertEqual(
            plan.impacted_units,
            ("xl-qqbot-prod.service", "xl-qqbot-router.service"),
        )
        self.assertEqual(plan.impacted_tiers, ("debug", "test", "production"))
        self.assertEqual(plan.announcement_scope, ("main",))

    def test_main_backend_change_pauses_and_notifies_production_tier(self):
        plan = plan_deployment(
            "main", "e" * 40, ["xl_updata_server/run_server.py"]
        )

        self.assertEqual(plan.impacted_units, ("xl-updata-server.service",))
        self.assertEqual(plan.impacted_tiers, ("production",))
        self.assertEqual(plan.announcement_scope, ("main",))

    def test_non_runtime_changes_remain_noop(self):
        for branch in ("debug", "test", "main"):
            with self.subTest(branch=branch):
                plan = plan_deployment(branch, "f" * 40, ["xl_updata_server/README.md"])
                self.assertEqual(plan.impacted_units, ())


if __name__ == "__main__":
    unittest.main()
