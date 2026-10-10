import tomllib
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
DEPLOY = ROOT / "xl_deploy" / "deploy"


def read(relative: str) -> str:
    return (ROOT / relative).read_text(encoding="utf-8")


def test_user_units_use_fixed_release_paths_and_keep_runtime_data_external():
    poller = read("xl_deploy/deploy/xl-deploy-poll.service")
    expected_services = {
        "xl-qqbot-debug.service": "debug",
        "xl-qqbot-test.service": "test",
        "xl-qqbot-prod.service": "main",
        "xl-qqbot-router.service": "main",
        "xl-updata-server.service": "main",
    }

    assert "systemctl --user" not in poller
    assert "python -m xl_deploy" in poller
    assert "/home/admin/xl_deploy/current/main/.venvs/xl-deploy-poll.service/bin/python" in poller
    assert "--config /home/admin/xl_deploy/config.toml" in poller
    assert "EnvironmentFile=" in poller
    for filename, branch in expected_services.items():
        content = (DEPLOY / filename).read_text(encoding="utf-8")
        assert f"/home/admin/xl_deploy/current/{branch}" in content
        assert "systemctl --user" not in content
        assert "sudo " not in content
        assert "[Install]" in content

    assert "WorkingDirectory=/home/admin/xl_qqbot-debug" in read(
        "xl_deploy/deploy/xl-qqbot-debug.service"
    )
    assert "WorkingDirectory=/home/admin/xl_qqbot-test" in read(
        "xl_deploy/deploy/xl-qqbot-test.service"
    )


def test_poller_timer_runs_every_minute_as_a_user_unit():
    timer = read("xl_deploy/deploy/xl-deploy-poll.timer")

    assert "OnBootSec=" in timer
    assert "OnUnitActiveSec=60s" in timer
    assert "Persistent=true" in timer
    assert "Unit=xl-deploy-poll.service" in timer


def test_sample_configuration_has_no_live_secrets_or_assetbundle_key():
    raw_sample = read("xl_deploy/config.toml.example")
    sample = raw_sample.lower()
    config = tomllib.loads(raw_sample)

    assert "assetbundle_key" not in sample
    assert "github_token" not in sample
    assert "deployment_token" not in sample
    assert "secrets.env" in read("xl_deploy/deploy/xl-deploy-poll.service")
    assert "/home/admin/xl_deploy/releases" in sample
    assert "/home/admin/xl_deploy/current" in sample
    assert "[deployment]" in sample
    assert "\nrepository =" in sample
    assert "\nstate_dir =" in sample
    assert "\nreleases_root =" in sample
    assert "\ncurrent_root =" in sample
    assert "\nbackend_config =" in sample
    assert "\nbackend_data =" in sample
    assert "\ntoken_file =" in sample
    assert set(config["github"]) == {"owner", "repo"}
    assert set(config["deployment"]) == {
        "root", "repository", "state_dir", "releases_root", "current_root",
        "backend_config", "backend_data", "test_backend_config", "test_backend_data",
    }
    assert set(config["router"]) == {"base_url", "token_file"}


def test_bootstrap_defaults_to_inventory_and_requires_explicit_activation():
    script = read("xl_deploy/scripts/bootstrap.sh")

    assert "--activate" in script
    assert "dry-run" in script.lower()
    assert "inventory" in script.lower()
    assert "systemctl --user" in script
    assert "chmod 600" in script
    assert "rm -" not in script
    assert "--migrate-existing" in script
    assert "cp -f" not in script
    assert "install -m 600" in script


def test_bootstrap_inventory_reads_existing_unit_mappings_and_configured_data_roots():
    script = read("xl_deploy/scripts/bootstrap.sh")

    assert 'for unit_file in "$unit_dir"/*.service' in script
    assert "WorkingDirectory" in script
    assert "EnvironmentFile" in script
    assert "data_dir" in script
    assert "configured data directory" in script
    assert 'report_config_mappings "$config_path" "$working_directory"' in script
    assert 'mapped = base_dir / mapped' in script
    assert 'config_path="$working_directory/$config_path"' in script
    assert 'systemd-analyze --user unit-paths' in script
    assert '"$unit_dir"/*.service.d/*.conf' in script
    assert '"backend_config", "backend_data"' in script
    assert 'systemctl --user show "$unit_name"' in script
    assert "effective_config" in script
    assert '[[ -z "$effective_config" && "$effective_exec" == *"--config"* ]]' in script
    assert 'if section == "watch" and working_directory is None:' in script
    assert "operator_inventory.py" in script


def test_operator_scripts_refuse_users_other_than_the_hardcoded_admin_account():
    bootstrap = read("xl_deploy/scripts/bootstrap.sh")
    doctor = read("xl_deploy/scripts/doctor.sh")

    assert 'if [[ "$HOME_DIR" != "/home/admin" ]]; then' in bootstrap
    assert 'if [[ "$HOME_DIR" != "/home/admin" ]]; then' in doctor


def test_bootstrap_never_invents_a_baseline_current_pointer():
    script = read("xl_deploy/scripts/bootstrap.sh")

    assert 'if [[ -e "$pointer" || -L "$pointer" ]]; then' in script
    assert "explicit release bootstrap" in script
    assert script.index('if [[ -e "$pointer" || -L "$pointer" ]]; then') < script.index(
        'if [[ ! -L "$pointer" ]]; then'
    )


def test_bootstrap_refuses_symlinked_managed_directories_and_existing_unit_links():
    script = read("xl_deploy/scripts/bootstrap.sh")

    assert 'if [[ -L "$directory" ]]; then' in script
    assert '[[ -e "$UNIT_DIR/$name" || -L "$UNIT_DIR/$name" ]]' in script
    assert 'if [[ -L "$CONFIG_FILE" ]]; then' in script


def test_bootstrap_has_explicit_reversible_existing_unit_migration():
    script = read("xl_deploy/scripts/bootstrap.sh")

    assert "--migrate-existing" in script
    assert "unit-backups" in script
    assert "systemctl --user is-active" in script
    assert "Refusing to migrate active unit" in script
    assert "migration manifest" in script.lower()
    assert 'mv -- "$UNIT_DIR/$name" "$backup_dir/$name"' in script
    assert 'existing_dropins+=("$name.d")' in script
    assert "drop-in %s" in script
    assert "Never automatically remove unit backups" in script


def test_bootstrap_requires_mode_600_for_every_credential_config():
    script = read("xl_deploy/scripts/bootstrap.sh")

    for path in (
        "$SECRETS_FILE",
        "$ROUTER_TOKEN_FILE",
    ):
        assert path in script
    assert "secret_mode" in script
    assert "router_token_mode" in script
    assert '[[ ! -f "$ROUTER_TOKEN_FILE" || -L "$ROUTER_TOKEN_FILE" ]]' in script
    assert "!= 600" in script
    assert "WorkingDirectory=" in script
    assert "xl-qqbot-test.service" in script


def test_doctor_is_read_only_and_reports_units_paths_and_secret_modes():
    script = read("xl_deploy/scripts/doctor.sh")

    assert "systemctl --user" in script
    assert "journalctl" in script
    assert "stat" in script
    assert "\nchmod " not in script
    assert "\nmkdir " not in script
    assert "\nrm " not in script
    assert '&& -L "$SECRET_DIR/router.token"' in script
    assert "router.token must be a regular file" in script


def test_operator_docs_cover_gate_order_recovery_and_at_least_once_notice():
    docs = read("xl_deploy/README.md") + read("xl_qqbot/docs/配置与运维.md")

    for required in (
        "XL CI",
        "精确 SHA",
        "检测到更新，正在更新bot，期间将暂停服务",
        "更新完毕",
        "回滚",
        "at-least-once",
        "chmod 600",
        "systemctl --user",
        "journalctl --user",
    ):
        assert required in docs


def test_ci_installs_schedule_renderer_dependencies_before_server_tests():
    workflow = read(".github/workflows/ci.yml")

    assert "actions/setup-node@v4" in workflow
    assert "npm ci --prefix renderer --omit=dev" in workflow


def test_docs_index_links_deployment_operator_guide():
    docs = read("docs/README.md")

    assert "xl_deploy/README.md" in docs
