import tomllib
import unittest
from pathlib import Path

from xl_deploy.planner import plan_deployment
from xl_deploy.runner import BRANCH_UNITS, CommandRunner, service_venv_path

ROOT = Path(__file__).resolve().parents[2]
DEPLOY = ROOT / "xl_deploy" / "deploy"


class TierServiceMappingTests(unittest.TestCase):
    def test_branch_release_contains_only_its_query_jobs_and_notifier_services(self):
        expected_test = {
            "xl-qqbot-test.service",
            "xl-qqbot-test-notify.service",
            "xl-qqbot-test-jobs.service",
            "xl-updata-server-test.service",
        }
        expected_main = {
            "xl-qqbot-prod.service",
            "xl-qqbot-router.service",
            "xl-qqbot-main-notify.service",
            "xl-qqbot-control.service",
            "xl-qqbot-main-jobs.service",
            "xl-updata-server.service",
            "xl-deploy-poll.service",
        }
        self.assertEqual(set(BRANCH_UNITS["test"]), expected_test)
        self.assertEqual(set(BRANCH_UNITS["main"]), expected_main)
        self.assertTrue(set(BRANCH_UNITS["test"]).isdisjoint(BRANCH_UNITS["main"]))

    def test_each_new_component_has_a_release_local_virtualenv(self):
        for branch, units in (("test", BRANCH_UNITS["test"]), ("main", BRANCH_UNITS["main"])):
            for unit in units:
                with self.subTest(branch=branch, unit=unit):
                    path = service_venv_path(Path("/srv/releases") / branch, unit)
                    self.assertEqual(path.parts[-2:], (".venvs", unit))

    def test_test_and_main_bot_changes_select_all_tier_owned_units(self):
        test = plan_deployment("test", "a" * 40, ["xl_qqbot/bot_app/automation.py"])
        main = plan_deployment("main", "b" * 40, ["xl_qqbot/bot_app/automation.py"])
        self.assertEqual(test.impacted_units, BRANCH_UNITS["test"][:3])
        self.assertEqual(main.impacted_units, BRANCH_UNITS["main"][:5])
        self.assertEqual(test.impacted_tiers, ("debug", "test"))
        self.assertEqual(main.impacted_tiers, ("debug", "test", "production"))

    def test_runtime_health_checks_use_each_component_loopback_port_and_auth(self):
        checked = []
        runner = CommandRunner(
            executor=lambda args, **_kwargs: None,
            health_token="a" * 40,
            health_timeout_seconds=1,
            health_poll_interval_seconds=0.01,
        )
        runner._wait_for_http_health = lambda request, unit: checked.append((request, unit))
        units = (
            "xl-qqbot-test.service",
            "xl-qqbot-prod.service",
            "xl-qqbot-router.service",
            "xl-qqbot-control.service",
            "xl-qqbot-test-notify.service",
            "xl-qqbot-main-notify.service",
            "xl-qqbot-test-jobs.service",
            "xl-qqbot-main-jobs.service",
        )
        runner.health_check(units)

        actual = {unit: request.full_url for request, unit in checked}
        self.assertEqual(actual["xl-qqbot-test.service"], "http://127.0.0.1:8782/health")
        self.assertEqual(actual["xl-qqbot-prod.service"], "http://127.0.0.1:8783/health")
        self.assertEqual(actual["xl-qqbot-router.service"], "http://127.0.0.1:8785/gateway/health")
        self.assertEqual(actual["xl-qqbot-control.service"], "http://127.0.0.1:8784/health")
        self.assertEqual(actual["xl-qqbot-test-notify.service"], "http://127.0.0.1:8786/health")
        self.assertEqual(actual["xl-qqbot-main-notify.service"], "http://127.0.0.1:8787/health")
        for request, unit in checked:
            if unit in {
                "xl-qqbot-router.service",
                "xl-qqbot-control.service",
                "xl-qqbot-test-notify.service",
                "xl-qqbot-main-notify.service",
            }:
                self.assertEqual(request.get_header("Authorization"), f"Bearer {'a' * 40}")

    def test_new_units_are_branch_pinned_isolated_and_loopback_components(self):
        expected = {
            "xl-qqbot-test-notify.service": ("test", "--instance-id test-bot"),
            "xl-qqbot-test-jobs.service": ("test", "--instance-id test-bot"),
            "xl-qqbot-main-notify.service": ("main", "--instance-id stable-bot"),
            "xl-qqbot-main-jobs.service": ("main", "--instance-id stable-bot"),
            "xl-qqbot-control.service": ("main", "-m bot_app.deployment_service"),
        }
        for unit, (branch, command) in expected.items():
            with self.subTest(unit=unit):
                content = (DEPLOY / unit).read_text(encoding="utf-8")
                self.assertIn(f"current/{branch}/", content)
                self.assertIn(command, content)
                self.assertIn(f"/home/admin/xl_qqbot{'-test' if branch == 'test' else ''}", content)
                if unit != "xl-qqbot-control.service":
                    self.assertIn(
                        f"xl-qqbot-{'test' if branch == 'test' else 'main'}", unit
                    )
                self.assertIn("NoNewPrivileges=true", content)
        sample_config = tomllib.loads(
            (ROOT / "xl_qqbot" / "config.toml.example").read_text(encoding="utf-8")
        )
        ports = (
            sample_config["router"]["test_port"],
            sample_config["router"]["production_port"],
            sample_config["router"]["deployment_control_port"],
            sample_config["gateway"]["control_port"],
            sample_config["notification"]["test_port"],
            sample_config["notification"]["production_port"],
        )
        self.assertEqual(len(set(ports)), len(ports))

    def test_bootstrap_installs_all_new_units_and_checks_their_external_configs(self):
        script = (ROOT / "xl_deploy" / "scripts" / "bootstrap.sh").read_text(
            encoding="utf-8"
        )
        for unit in (
            "xl-qqbot-test-notify.service",
            "xl-qqbot-main-notify.service",
            "xl-qqbot-control.service",
            "xl-qqbot-test-jobs.service",
            "xl-qqbot-main-jobs.service",
        ):
            with self.subTest(unit=unit):
                self.assertIn(unit, script)


if __name__ == "__main__":
    unittest.main()
