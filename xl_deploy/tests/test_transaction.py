from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest

from xl_deploy.planner import plan_deployment
from xl_deploy.state import DeploymentState
from xl_deploy.transaction import DeploymentPaths, DeploymentTransaction

OLD_SHA = "1" * 40
NEW_SHA = "2" * 40
START_NOTICE = "检测到更新，正在更新bot，期间将暂停服务"
DONE_NOTICE = "更新完毕"


class FakeRunner:
    def __init__(self):
        self.events: list[tuple[object, ...]] = []
        self.fail_health_for: set[str] = set()
        self.fail_start_for: set[str] = set()

    def stage(self, plan, release_path: Path) -> Path:
        self.events.append(("stage", plan.sha))
        release_path.mkdir(parents=True, exist_ok=False)
        return release_path

    def preflight(self, plan, release_path, config_path, data_path):
        self.events.append(("preflight", plan.sha))
        assert config_path.is_absolute() and data_path.is_absolute()
        assert not config_path.is_relative_to(release_path)
        assert not data_path.is_relative_to(release_path)

    def stop(self, units):
        self.events.append(("stop", *units))

    def start(self, units):
        self.events.append(("start", *units))
        if any(unit in self.fail_start_for for unit in units):
            raise RuntimeError("injected start failure")

    def health_check(self, units):
        self.events.append(("health", *units))
        if any(unit in self.fail_health_for for unit in units):
            self.fail_health_for.difference_update(units)
            raise RuntimeError("injected health failure")


class FakeRouter:
    def __init__(self):
        self.events: list[tuple[object, ...]] = []
        self.announce_ok = True
        self.announce_results: list[bool] = []
        self.drain_ok = True
        self.maintenance: set[str] = set()
        self.release_note_results: list[bool] = []

    def announce(self, scope, text):
        self.events.append(("announce", text, *scope))
        return self.announce_results.pop(0) if self.announce_results else self.announce_ok

    def pause(self, tiers):
        self.events.append(("pause", *tiers))
        self.maintenance.update(tiers)

    def drain(self, tiers, timeout_seconds):
        self.events.append(("drain", *tiers))
        return self.drain_ok

    def resume(self, tiers):
        self.events.append(("resume", *tiers))
        self.maintenance.difference_update(tiers)

    def announce_release_note(self, scope, text):
        self.events.append(("release_note", text, *scope))
        return self.release_note_results.pop(0) if self.release_note_results else True


class FakeCurrentPointer:
    def __init__(self):
        self.targets: dict[str, Path] = {}

    def read(self, pointer):
        return self.targets.get(str(pointer))

    def replace(self, pointer, target):
        self.targets[str(pointer)] = target


def make_transaction(tmp_path, *, runner=None, router=None, drain_timeout=3):
    root = tmp_path / "deploy"
    releases = root / "releases"
    releases.mkdir(parents=True)
    old_releases = {
        "debug": releases / OLD_SHA,
        "test": releases / ("3" * 40),
        "main": releases / ("4" * 40),
    }
    for release in old_releases.values():
        release.mkdir()
    paths = DeploymentPaths(
        releases_root=releases,
        current_path=root / "current",
        config_path=root / "shared" / "config.toml",
        data_path=root / "shared" / "data",
    )
    state = DeploymentState(
        root / "state",
        releases_root=releases,
        current_path=root / "current",
        pointer_adapter=FakeCurrentPointer(),
    )
    for branch, release in old_releases.items():
        state.set_current(release, current_path=paths.current_path / branch)
    paths.config_path.parent.mkdir(parents=True)
    paths.config_path.write_text("config stays outside releases", encoding="utf-8")
    paths.data_path.mkdir(parents=True)
    return DeploymentTransaction(
        state,
        runner or FakeRunner(),
        router or FakeRouter(),
        paths,
        drain_timeout_seconds=drain_timeout,
    )


def make_plan(*, sha=NEW_SHA, release_note=None):
    return plan_deployment(
        "debug", sha, ["xl_qqbot/bot_app/router.py"], release_note=release_note
    )


def branch_current(transaction, branch):
    return transaction.state.get_current(
        current_path=transaction.paths.current_path / branch
    )


def combined_events(runner, router):
    return sorted(runner.events + router.events, key=lambda event: event[0])


def test_happy_path_obeys_notice_pause_drain_stop_switch_start_health_resume_order(tmp_path):
    runner, router = FakeRunner(), FakeRouter()
    transaction = make_transaction(tmp_path, runner=runner, router=router)

    result = transaction.execute(make_plan(release_note="New version details"))

    assert result.status == "success"
    assert [event[0] for event in transaction.events] == [
        "stage", "preflight", "announce", "pause", "drain", "stop", "switch",
        "start", "health", "resume", "announce", "release_note",
    ]
    assert transaction.events[2] == ("announce", START_NOTICE, "debug")
    assert transaction.events[9] == ("resume", "debug")
    assert transaction.events[10] == ("announce", DONE_NOTICE, "debug")
    assert transaction.events[11] == ("release_note", "New version details", "debug")
    assert branch_current(transaction, "debug").name.startswith(f"{NEW_SHA}-")
    assert branch_current(transaction, "test").name == "3" * 40
    assert branch_current(transaction, "main").name == "4" * 40


def test_notice_failure_never_pauses_or_stops_live_services(tmp_path):
    router = FakeRouter()
    router.announce_ok = False
    transaction = make_transaction(tmp_path, router=router)

    with pytest.raises(RuntimeError, match="notice"):
        transaction.execute(make_plan())

    assert not any(event[0] in {"pause", "drain", "stop", "switch"} for event in transaction.events)
    assert branch_current(transaction, "debug").name == OLD_SHA


def test_drain_timeout_aborts_before_stopping_units(tmp_path):
    router = FakeRouter()
    router.drain_ok = False
    transaction = make_transaction(tmp_path, router=router, drain_timeout=1)

    with pytest.raises(TimeoutError, match="drain"):
        transaction.execute(make_plan())

    assert not any(event[0] in {"stop", "switch", "start"} for event in transaction.events)
    assert branch_current(transaction, "debug").name == OLD_SHA
    assert router.maintenance == set()


def test_invalid_sha_is_rejected_before_staging_or_command_execution(tmp_path):
    transaction = make_transaction(tmp_path)

    with pytest.raises(ValueError, match="SHA"):
        transaction.execute(make_plan(sha="../../main;touch-pwned"))

    assert not transaction.events


def test_data_and_config_cannot_be_located_inside_immutable_releases(tmp_path):
    releases = tmp_path / "deploy" / "releases"
    with pytest.raises(ValueError, match="config_path.*outside"):
        DeploymentPaths(
            releases_root=releases,
            current_path=tmp_path / "deploy" / "current",
            config_path=releases / "config.toml",
            data_path=tmp_path / "shared" / "data",
        )
    with pytest.raises(ValueError, match="data_path.*outside"):
        DeploymentPaths(
            releases_root=releases,
            current_path=tmp_path / "deploy" / "current",
            config_path=tmp_path / "shared" / "config.toml",
            data_path=releases / "data",
        )


def test_deployment_paths_do_not_follow_current_pointer_leaf(tmp_path, monkeypatch):
    root = tmp_path / "deploy"
    releases = root / "releases"
    old = releases / OLD_SHA
    old.mkdir(parents=True)
    current = root / "current"
    original_resolve = Path.resolve

    def resolve_with_current_symlink(path, *args, **kwargs):
        if path == current:
            return old
        return original_resolve(path, *args, **kwargs)

    monkeypatch.setattr(Path, "resolve", resolve_with_current_symlink)

    paths = DeploymentPaths(
        releases_root=releases,
        current_path=current,
        config_path=root / "shared" / "config.toml",
        data_path=root / "shared" / "data",
    )

    assert paths.current_path == current.absolute()


def test_backend_only_deployment_does_not_pause_or_announce_to_bot(tmp_path):
    runner, router = FakeRunner(), FakeRouter()
    transaction = make_transaction(tmp_path, runner=runner, router=router)
    plan = plan_deployment("main", NEW_SHA, ["xl_updata_server/server_app/processor.py"])

    result = transaction.execute(plan)

    assert result.status == "success"
    assert plan.impacted_units == ("xl-updata-server.service",)
    assert not any(event[0] in {"announce", "pause", "drain", "resume"} for event in transaction.events)
    assert transaction.events.index(("stop", "xl-updata-server.service")) < transaction.events.index(
        ("switch", NEW_SHA)
    )
    assert branch_current(transaction, "main").name.startswith(f"{NEW_SHA}-")
    assert branch_current(transaction, "debug").name == OLD_SHA
    assert branch_current(transaction, "test").name == "3" * 40


def test_debug_update_leaves_test_and_main_current_pointers_untouched(tmp_path):
    transaction = make_transaction(tmp_path)
    previous_test = branch_current(transaction, "test")
    previous_main = branch_current(transaction, "main")

    result = transaction.execute(make_plan())

    assert result.status == "success"
    assert branch_current(transaction, "debug").name.startswith(f"{NEW_SHA}-")
    assert branch_current(transaction, "test") == previous_test
    assert branch_current(transaction, "main") == previous_main


def test_health_failure_rolls_back_and_retains_both_releases(tmp_path):
    runner, router = FakeRunner(), FakeRouter()
    runner.fail_health_for.add("xl-qqbot-debug.service")
    transaction = make_transaction(tmp_path, runner=runner, router=router)

    result = transaction.execute(make_plan())

    assert result.status == "rolled_back"
    assert branch_current(transaction, "debug").name == OLD_SHA
    assert Path(transaction.state.load().candidate_release).exists()
    assert transaction.state.load().phase == "rolled_back"
    assert transaction.events[-1] == ("announce", "部署失败，已回滚到上一版本。", "debug")


def test_rollback_failure_keeps_maintenance_and_journal_evidence(tmp_path):
    runner, router = FakeRunner(), FakeRouter()
    runner.fail_health_for.add("xl-qqbot-debug.service")
    runner.fail_start_for.add("xl-qqbot-debug.service")
    transaction = make_transaction(tmp_path, runner=runner, router=router)

    result = transaction.execute(make_plan())

    assert result.status == "rollback_failed"
    assert router.maintenance == {"debug"}
    journal = transaction.state.load()
    assert journal.phase == "rollback_failed"
    assert journal.maintenance_enabled is True
    assert journal.evidence["rollback_error"]
    assert transaction.state.load().candidate_release.endswith(NEW_SHA) is False
    assert Path(transaction.state.load().candidate_release).exists()

    runner.fail_start_for.clear()
    runner.fail_health_for.clear()
    recovered = transaction.recover()
    assert recovered.status == "rolled_back"
    assert router.maintenance == set()
    assert transaction.state.load().phase == "rolled_back"


def test_recovery_after_interruption_is_idempotent(tmp_path):
    runner, router = FakeRunner(), FakeRouter()

    class InterruptingRunner(FakeRunner):
        interrupted = False

        def start(self, units):
            super().start(units)
            if not self.interrupted and transaction.state.load().phase == "starting":
                self.interrupted = True
                raise KeyboardInterrupt

    runner = InterruptingRunner()
    transaction = make_transaction(tmp_path, runner=runner, router=router)
    with pytest.raises(KeyboardInterrupt):
        transaction.execute(make_plan())

    first = transaction.recover()
    event_count = len(transaction.events)
    second = transaction.recover()

    assert first.status == "rolled_back"
    assert second.status == "already_recovered"
    assert len(transaction.events) == event_count
    assert branch_current(transaction, "debug").name == OLD_SHA
    assert transaction.state.load().phase == "rolled_back"


def test_failed_completion_notice_is_persisted_and_retried(tmp_path):
    router = FakeRouter()
    router.announce_results = [True, False, True]
    transaction = make_transaction(tmp_path, router=router)

    result = transaction.execute(make_plan(release_note="Release notes"))

    assert result.status == "announcement_pending"
    assert transaction.state.load().phase == "completion_notice_pending"
    assert transaction.state.load().release_note == "Release notes"
    recovered = transaction.recover()

    assert recovered.status == "success"
    assert transaction.state.load().phase == "completed"
    assert transaction.state.load().completion_notice_sent is True
    assert transaction.state.load().release_note_sent is True
    assert [event[0] for event in transaction.events[-2:]] == ["announce", "release_note"]


def test_new_deployment_is_blocked_until_pending_notice_is_recovered(tmp_path):
    router = FakeRouter()
    router.announce_results = [True, False]
    transaction = make_transaction(tmp_path, router=router)

    result = transaction.execute(make_plan())

    assert result.status == "announcement_pending"
    with pytest.raises(RuntimeError, match="pending deployment must be recovered"):
        transaction.execute(make_plan(sha="5" * 40))


def test_failed_release_note_is_persisted_and_retried_without_rollback(tmp_path):
    router = FakeRouter()
    router.release_note_results = [False, True]
    transaction = make_transaction(tmp_path, router=router)

    result = transaction.execute(make_plan(release_note="New public version details"))

    assert result.status == "announcement_pending"
    assert transaction.state.load().phase == "release_note_pending"
    assert branch_current(transaction, "debug").name.startswith(f"{NEW_SHA}-")
    recovered = transaction.recover()

    assert recovered.status == "success"
    assert transaction.state.load().phase == "completed"
    assert transaction.state.load().release_note_sent is True
    assert branch_current(transaction, "debug").name.startswith(f"{NEW_SHA}-")


def test_recovery_after_router_resume_retries_completion_instead_of_rolling_back(tmp_path):
    runner, router = FakeRunner(), FakeRouter()
    transaction = make_transaction(tmp_path, runner=runner, router=router)

    class InterruptAfterResume(FakeRouter):
        interrupted = False

        def resume(self, tiers):
            super().resume(tiers)
            if not self.interrupted and transaction.state.load().phase == "resuming":
                self.interrupted = True
                raise KeyboardInterrupt

    router = InterruptAfterResume()
    transaction.router = router
    with pytest.raises(KeyboardInterrupt):
        transaction.execute(make_plan(release_note="Release notes"))

    assert transaction.state.load().phase == "resuming"
    recovered = transaction.recover()

    assert recovered.status == "success"
    assert branch_current(transaction, "debug").name.startswith(f"{NEW_SHA}-")
    assert transaction.state.load().phase == "completed"
    assert router.maintenance == set()


def test_failed_recovery_notice_is_persisted_and_retried(tmp_path):
    runner, router = FakeRunner(), FakeRouter()
    runner.fail_health_for.add("xl-qqbot-debug.service")
    router.announce_results = [True, False, True]
    transaction = make_transaction(tmp_path, runner=runner, router=router)

    result = transaction.execute(make_plan())

    assert result.status == "announcement_pending"
    assert transaction.state.load().phase == "recovery_notice_pending"
    assert branch_current(transaction, "debug").name == OLD_SHA
    recovered = transaction.recover()

    assert recovered.status == "rolled_back"
    assert transaction.state.load().phase == "rolled_back"
    assert transaction.state.load().recovery_notice_sent is True


def test_process_command_runner_rejects_non_allowlisted_units(tmp_path):
    from xl_deploy.runner import CommandRunner

    command_runner = CommandRunner(executor=lambda args, **kwargs: None)
    with pytest.raises(ValueError, match="allowlist"):
        command_runner.systemctl("stop", ("attacker.service; touch /tmp/pwned",))
    command_runner.systemctl("stop", ("xl-qqbot-prod.service",))
    with pytest.raises(ValueError, match="allowlist"):
        command_runner.systemctl("stop", ("xl-qqbot-production.service",))


def test_command_runner_stages_exact_sha_as_argv_without_branch_interpolation(tmp_path):
    from xl_deploy.runner import CommandRunner

    commands = []
    repository = tmp_path / "repo"
    repository.mkdir()
    candidate = tmp_path / "releases" / NEW_SHA
    runner = CommandRunner(repository=repository, executor=lambda args, **kwargs: commands.append(args))

    result = runner.stage(SimpleNamespace(sha=NEW_SHA, branch="main; touch nope"), candidate)

    assert result == candidate
    assert commands == [
        ["git", "fetch", "--no-tags", "origin", NEW_SHA],
        ["git", "worktree", "add", "--detach", str(candidate), NEW_SHA],
    ]


def test_failed_preflight_keeps_unique_artifact_and_same_sha_can_retry(tmp_path):
    runner, router = FakeRunner(), FakeRouter()

    class FailFirstPreflight(FakeRunner):
        def __init__(self):
            super().__init__()
            self.candidates = []
            self.fail_once = True

        def preflight(self, plan, release_path, config_path, data_path):
            self.candidates.append(release_path)
            super().preflight(plan, release_path, config_path, data_path)
            if self.fail_once:
                self.fail_once = False
                raise RuntimeError("injected preflight failure")

    runner = FailFirstPreflight()
    transaction = make_transaction(tmp_path, runner=runner, router=router)
    with pytest.raises(RuntimeError, match="preflight"):
        transaction.execute(make_plan())
    failed_candidate = runner.candidates[0]
    assert failed_candidate.exists()
    assert transaction.state.load().phase == "preflight_failed"

    result = transaction.execute(make_plan())

    assert result.status == "success"
    assert len(runner.candidates) == 2
    assert runner.candidates[0] != runner.candidates[1]
    assert all(path.exists() for path in runner.candidates)
    assert branch_current(transaction, "debug") == runner.candidates[1].resolve()


def test_preflight_creates_one_release_local_venv_per_impacted_unit(tmp_path):
    from xl_deploy.runner import CommandRunner, service_venv_path

    commands = []
    release = tmp_path / "release"
    (release / "xl_qqbot").mkdir(parents=True)
    (release / "xl_updata_server").mkdir()
    bot_requirements = release / "xl_qqbot" / "requirements.txt"
    backend_requirements = release / "xl_updata_server" / "requirements.txt"
    bot_requirements.write_text("bot-only==1\n", encoding="utf-8")
    backend_requirements.write_text("backend-only==1\n", encoding="utf-8")
    plan = plan_deployment("main", NEW_SHA, ["xl_updata_server/server_app/processor.py"])
    runner = CommandRunner(executor=lambda args, **kwargs: commands.append((args, kwargs)))

    runner.preflight(
        plan,
        release,
        tmp_path / "shared" / "config.toml",
        tmp_path / "shared" / "data",
    )

    venv_commands = [args for args, _ in commands if args[1:3] == ["-m", "venv"]]
    assert plan.impacted_units == ("xl-updata-server.service",)
    assert len(venv_commands) == 4
    venv_paths = [Path(args[-1]) for args in venv_commands]
    assert len(set(venv_paths)) == 4
    assert all(path.is_relative_to(release / ".venvs") for path in venv_paths)
    install_commands = [args for args, _ in commands if "pip" in args]
    assert len(install_commands) == 3
    assert sum(str(bot_requirements) in args for args in install_commands) == 2
    assert sum(str(backend_requirements) in args for args in install_commands) == 1
    assert {Path(args[0]).parent.parent for args in install_commands} == {
        service_venv_path(release, unit)
        for unit in (
            "xl-qqbot-prod.service",
            "xl-qqbot-router.service",
            "xl-updata-server.service",
        )
    }


def test_preflight_compiles_backend_sources_before_any_service_stop(tmp_path):
    from xl_deploy.runner import CommandRunner

    release = tmp_path / "release"
    backend = release / "xl_updata_server" / "server_app"
    backend.mkdir(parents=True)
    (release / "xl_updata_server" / "requirements.txt").write_text("", encoding="utf-8")
    (release / "xl_qqbot").mkdir(parents=True)
    (release / "xl_qqbot" / "requirements.txt").write_text("", encoding="utf-8")
    (backend / "broken.py").write_text("def invalid(:\n", encoding="utf-8")
    plan = plan_deployment("main", NEW_SHA, ["xl_updata_server/server_app/processor.py"])
    runner = CommandRunner(executor=lambda args, **kwargs: None)

    with pytest.raises(SyntaxError):
        runner.preflight(
            plan,
            release,
            tmp_path / "shared" / "config.toml",
            tmp_path / "shared" / "data",
        )


def test_backend_health_check_runs_read_only_application_self_check(tmp_path, monkeypatch):
    from xl_deploy.runner import CommandRunner

    commands = []
    runner = CommandRunner(
        executor=lambda args, **kwargs: commands.append(args),
        backend_config_path=tmp_path / "backend.toml",
        deployment_root=tmp_path / "deploy",
    )

    def forbidden_http(*args, **kwargs):
        raise AssertionError("backend does not expose an HTTP health endpoint")

    monkeypatch.setattr("urllib.request.urlopen", forbidden_http)
    runner.health_check(("xl-updata-server.service",))

    assert commands[0] == [
        "systemctl", "--user", "is-active", "--quiet", "xl-updata-server.service"
    ]
    assert commands[1][1].replace("\\", "/").endswith("xl_updata_server/run_server.py")
    assert commands[1][2:] == ["--healthcheck", "--config", str(tmp_path / "backend.toml")]


def test_router_health_check_authenticates_and_retries_until_ready(monkeypatch):
    import urllib.error

    from xl_deploy.runner import CommandRunner

    commands = []

    class Response:
        status = 200

        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return False

    attempts = 0

    def urlopen(request, timeout):
        nonlocal attempts
        attempts += 1
        assert request.get_header("Authorization") == "Bearer local-token"
        if attempts == 1:
            raise urllib.error.HTTPError(request.full_url, 503, "starting", {}, None)
        return Response()

    monkeypatch.setattr("urllib.request.urlopen", urlopen)
    monkeypatch.setattr("time.sleep", lambda _seconds: None)
    runner = CommandRunner(
        executor=lambda args, **kwargs: commands.append(args),
        health_token="local-token",
        health_timeout_seconds=1,
        health_poll_interval_seconds=0.001,
    )

    runner.health_check(("xl-qqbot-router.service",))

    assert attempts == 2
    assert commands == [[
        "systemctl", "--user", "is-active", "--quiet", "xl-qqbot-router.service"
    ]]


def test_router_health_check_fails_closed_without_token():
    from xl_deploy.runner import CommandRunner

    runner = CommandRunner(executor=lambda _args, **_kwargs: None)

    with pytest.raises(RuntimeError, match="token is not configured"):
        runner.health_check(("xl-qqbot-router.service",))
