from pathlib import Path


def test_deployment_scripts_check_node_renderer_and_low_concurrency_entrypoint():
    assert "Pillow" not in Path("requirements.txt").read_text(encoding="utf-8")
    assert Path("renderer/package.json").is_file()
    assert Path("scripts/install_renderer.sh").is_file()
    script = Path("scripts/run_full_production.sh").read_text(encoding="utf-8")
    assert "systemd-run" in script
    assert "concurrency=1" in script
    assert "--property=WorkingDirectory=\"$SERVER_DIR\"" in script
    assert "--working-directory" not in script
    assert "--property=CPUQuota=100%" in script
    assert "--property=CPUAffinity=0" in script
