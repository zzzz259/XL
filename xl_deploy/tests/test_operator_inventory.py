import pytest

from xl_deploy.operator_inventory import extract_systemd_config


@pytest.mark.parametrize(
    ("exec_start", "expected"),
    [
        (
            "{ path=/usr/bin/python ; argv[]=/usr/bin/python ; argv[]=-m ; "
            + "argv[]=server ; argv[]=--config ; argv[]=/home/admin/xl/config.toml ; ignore_errors=no }",
            "/home/admin/xl/config.toml",
        ),
        (
            "{ path=/usr/bin/python ; argv[]=/usr/bin/python /home/admin/server.py "
            + "--config /home/admin/xl/config.toml ; ignore_errors=no }",
            "/home/admin/xl/config.toml",
        ),
        (
            "{ path=/usr/bin/python ; argv[]=/usr/bin/python ; argv[]=run.py ; "
            + "argv[]=--config=relative.toml ; ignore_errors=no }",
            "relative.toml",
        ),
        ("{ path=/usr/bin/true ; argv[]=/usr/bin/true ; ignore_errors=no }", None),
    ],
)
def test_extracts_effective_config_argument_from_systemd_argv(exec_start, expected):
    assert extract_systemd_config(exec_start) == expected
