import pytest

from xl_deploy.planner import DeploymentPlan, DeploymentTarget, plan_deployment


def test_debug_and_test_branches_target_only_their_own_tier():
    debug = plan_deployment("debug", "d1", ["xl_qqbot/bot_app/service.py"])
    test = plan_deployment("test", "t1", ["xl_qqbot/bot_app/service.py"])

    assert debug.impacted_tiers == ("debug",)
    assert debug.impacted_units == ("xl-qqbot-debug.service",)
    assert debug.announcement_scope == ("debug",)
    assert test.impacted_tiers == ("test",)
    assert test.impacted_units == ("xl-qqbot-test.service",)


@pytest.mark.parametrize(
    ("paths", "units", "tiers", "notice"),
    [
        (["xl_qqbot/bot_app/router.py"], ("xl-qqbot-prod.service", "xl-qqbot-router.service"), ("debug", "test", "production"), ("main",)),
        (["xl_updata_server/server_app/processor.py"], ("xl-updata-server.service",), (), ()),
        (["xl_qqbot/bot_app/service.py", "xl_updata_server/run_server.py"],
         ("xl-qqbot-prod.service", "xl-qqbot-router.service", "xl-updata-server.service"),
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


def test_deployment_target_is_an_explicit_immutable_mapping():
    target = DeploymentTarget.for_changes("debug", ["xl_qqbot/bot_app/service.py"])

    assert target.impacted_tiers == ("debug",)
    assert target.impacted_units == ("xl-qqbot-debug.service",)
    with pytest.raises((AttributeError, TypeError)):
        target.impacted_units = ()
