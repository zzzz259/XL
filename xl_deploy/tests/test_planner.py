import pytest

from xl_deploy.planner import DeploymentPlan, DeploymentTarget, plan_deployment


def test_debug_branch_is_noop_while_test_bot_targets_both_lower_tiers():
    debug = plan_deployment("debug", "d1", ["xl_qqbot/bot_app/service.py"])
    test = plan_deployment("test", "t1", ["xl_qqbot/bot_app/service.py"])

    assert debug.impacted_tiers == ()
    assert debug.impacted_units == ()
    assert debug.announcement_scope == ()
    assert test.impacted_tiers == ("debug", "test")
    assert test.impacted_units == (
        "xl-qqbot-test.service",
        "xl-qqbot-test-notify.service",
        "xl-qqbot-test-jobs.service",
    )
    assert test.announcement_scope == ("debug", "test")


@pytest.mark.parametrize(
    ("paths", "units", "tiers", "notice"),
    [
        (["xl_qqbot/bot_app/router.py"],
         ("xl-qqbot-prod.service", "xl-qqbot-router.service", "xl-qqbot-main-notify.service", "xl-qqbot-control.service", "xl-qqbot-main-jobs.service"),
         ("debug", "test", "production"), ("main",)),
        (["xl_updata_server/server_app/processor.py"], ("xl-updata-server.service",), ("production",), ("main",)),
        (["xl_qqbot/bot_app/service.py", "xl_updata_server/run_server.py"],
         ("xl-qqbot-prod.service", "xl-qqbot-router.service", "xl-qqbot-main-notify.service", "xl-qqbot-control.service", "xl-qqbot-main-jobs.service", "xl-updata-server.service"),
         ("debug", "test", "production"), ("main",)),
    ],
)
def test_main_maps_changed_paths_to_only_affected_units(paths, units, tiers, notice):
    plan = plan_deployment("main", "m1", paths)

    assert plan.impacted_units == units
    assert plan.impacted_tiers == tiers
    assert plan.announcement_scope == notice


@pytest.mark.parametrize(
    "branch,paths",
    [
        ("debug", ["README.md", "docs/ops.md"]),
        ("test", ["xl_updata_server/README.md"]),
        ("main", ["README.md", "docs/architecture.md", ".github/workflows/ci.yml"]),
    ],
)
def test_docs_and_unrelated_changes_are_noop(branch, paths):
    plan = plan_deployment(branch, "sha", paths)

    assert plan.impacted_units == ()
    assert plan.impacted_tiers == ()
    assert plan.announcement_scope == ()


def test_plan_is_immutable_and_normalizes_changed_path_separators():
    plan = plan_deployment("debug", "sha", [r"xl_qqbot\bot_app\service.py"])

    assert isinstance(plan, DeploymentPlan)
    assert plan.changed_paths == ("xl_qqbot/bot_app/service.py",)
    with pytest.raises((AttributeError, TypeError)):
        plan.sha = "other"


def test_unknown_branch_is_rejected():
    with pytest.raises(ValueError):
        plan_deployment("feature", "sha", ["xl_qqbot/bot_app/service.py"])


def test_gacha_web_runtime_changes_map_to_bot_units_on_test_and_main():
    test = plan_deployment("test", "t2", ["xl_gacha_web/gacha_core/leaderboards.py"])
    main = plan_deployment("main", "m2", ["xl_gacha_web/leaderboard_archive.py"])

    assert test.impacted_units == (
        "xl-qqbot-test.service",
        "xl-qqbot-test-notify.service",
        "xl-qqbot-test-jobs.service",
    )
    assert test.impacted_tiers == ("debug", "test")
    assert main.impacted_units == (
        "xl-qqbot-prod.service",
        "xl-qqbot-router.service",
        "xl-qqbot-main-notify.service",
        "xl-qqbot-control.service",
        "xl-qqbot-main-jobs.service",
    )
    assert main.impacted_tiers == ("debug", "test", "production")


def test_gacha_web_change_combined_with_qqbot_does_not_duplicate_units():
    plan = plan_deployment(
        "test",
        "t3",
        ["xl_qqbot/bot_app/service.py", "xl_gacha_web/gacha_core/gacha.py"],
    )

    assert plan.impacted_units == (
        "xl-qqbot-test.service",
        "xl-qqbot-test-notify.service",
        "xl-qqbot-test-jobs.service",
    )


@pytest.mark.parametrize(
    "paths",
    [
        ["xl_gacha_web/docs/leaderboard-archive.md"],
        ["xl_gacha_web/tests/test_core.py"],
        ["xl_gacha_web/tests/fixtures/catalog-contract.json"],
        ["xl_gacha_web/README.md"],
    ],
)
def test_gacha_web_docs_tests_and_readme_do_not_trigger(paths):
    for branch in ("debug", "test", "main"):
        plan = plan_deployment(branch, "sha", paths)

        assert plan.impacted_units == ()
        assert plan.impacted_tiers == ()
        assert plan.announcement_scope == ()


def test_deployment_target_is_an_explicit_immutable_mapping():
    target = DeploymentTarget.for_changes("debug", ["xl_qqbot/bot_app/service.py"])

    assert target.impacted_tiers == ()
    assert target.impacted_units == ()
    with pytest.raises((AttributeError, TypeError)):
        target.impacted_units = ()
