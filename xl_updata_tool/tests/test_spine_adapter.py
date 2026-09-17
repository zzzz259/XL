from types import SimpleNamespace

import pytest

from app.features.preview.spine_adapter import (
    SpineQueryRunner,
    parse_skin_query_output,
)


def test_parse_skin_query_output_ignores_headers_and_empty_lines():
    assert parse_skin_query_output("Skin:\nbase\nfestival\n\n") == ("base", "festival")


def test_query_skins_uses_authoritative_skin_command(monkeypatch, tmp_path):
    calls = []

    def fake_run(command, **kwargs):
        calls.append((command, kwargs))
        return SimpleNamespace(returncode=0, stdout="Skin:\nbase\nfestival\n", stderr="")

    monkeypatch.setattr("app.features.preview.spine_adapter.subprocess.run", fake_run)
    cli = tmp_path / "SpineViewerCLI.exe"
    runner = SpineQueryRunner(str(cli))

    result = runner.query_skins(str(tmp_path / "hero.skel"), str(tmp_path / "hero.atlas"))

    assert result.skin_names == ("base", "festival")
    assert calls[0][0] == [
        str(cli),
        "query",
        str(tmp_path / "hero.skel"),
        "--atlas",
        str(tmp_path / "hero.atlas"),
        "--skin",
    ]


def test_query_skins_preserves_cli_failure_details(monkeypatch, tmp_path):
    def fake_run(_command, **_kwargs):
        return SimpleNamespace(returncode=17, stdout="", stderr="atlas parse failed")

    monkeypatch.setattr("app.features.preview.spine_adapter.subprocess.run", fake_run)
    result = SpineQueryRunner("SpineViewerCLI.exe").query_skins(
        str(tmp_path / "hero.skel"), str(tmp_path / "hero.atlas")
    )

    assert result.skin_names == ()
    assert result.returncode == 17
    assert result.stderr == "atlas parse failed"
    assert not result.ok


def test_query_skins_preserves_timeout_as_failure(monkeypatch, tmp_path):
    def fake_run(_command, **_kwargs):
        raise pytest.importorskip("subprocess").TimeoutExpired("query", 15)

    monkeypatch.setattr("app.features.preview.spine_adapter.subprocess.run", fake_run)
    result = SpineQueryRunner("SpineViewerCLI.exe").query_skins(
        str(tmp_path / "hero.skel"), str(tmp_path / "hero.atlas")
    )

    assert result.timed_out
    assert "timed out" in result.error
