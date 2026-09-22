from pathlib import Path


def test_server_python_sources_compile():
    root = Path(__file__).parents[1]
    sources = list((root / "server_app").rglob("*.py")) + [root / "run_server.py"]
    assert sources
    for source in sources:
        compile(source.read_text(encoding="utf-8"), str(source), "exec")


def test_deployment_files_exist():
    root = Path(__file__).parents[1]
    assert (root / "deploy" / "xl-updata-server.service").is_file()
    assert (root / "scripts" / "install_server.sh").is_file()
    assert (root / "scripts" / "check_server.sh").is_file()
