from types import SimpleNamespace

import pytest

from app.features.preview.spine_adapter import (
    SpineQueryRunner,
    parse_skin_query_output,
)


REALISTIC_MIXED_QUERY_OUTPUT = """
[INFO] Querying skeleton hero.skel
SpineViewerCLI 1.4.2
Skins:
base
festival
[DEBUG] resolved 2 skin entries
2026-09-17 12:00:00 query heartbeat
Query completed successfully
not a skin entry
Attachments:
Attachment: skin=base;slot=body;name=body_region
Animations:
idle
walk
Slots:
body
head
[INFO] Query completed successfully
"""


def test_parse_skin_query_output_ignores_headers_and_empty_lines():
    assert parse_skin_query_output(
        "Skin:\nbase\nfestival\n\nAttachments:\n"
        "Attachment: skin=base;slot=body;name=body_region\n"
    ) == ("base", "festival")


def test_parse_skin_query_output_reads_only_skin_section_from_mixed_query_output():
    assert parse_skin_query_output(REALISTIC_MIXED_QUERY_OUTPUT) == ("base", "festival")


def test_parse_skin_query_output_accepts_indented_and_list_skin_entries():
    assert parse_skin_query_output("Skins:\n  base\n- festival\nAnimations:\nidle\n") == (
        "base", "festival"
    )


def test_parse_skin_query_output_ignores_arbitrary_indented_prose():
    assert parse_skin_query_output("Skins:\n  not a skin entry\n") == ()


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


def test_query_skins_hashes_normalized_attachment_sets(monkeypatch, tmp_path):
    outputs = iter(
        [
            "Skin: base\n"
            "Attachment: skin=base;slot=body;name=body_region\n"
            "Attachment: skin=base;slot=face;name=face_region\n",
            "Skin: base\n"
            "Attachment: skin=base;slot=face;name=face_region\n"
            "Attachment: skin=base;slot=body;name=body_region\n",
            "Skin: base\n"
            "Attachment: skin=base;slot=body;name=other_region\n"
            "Attachment: skin=base;slot=face;name=face_region\n",
        ]
    )

    def fake_run(_command, **_kwargs):
        return SimpleNamespace(returncode=0, stdout=next(outputs), stderr="")

    monkeypatch.setattr("app.features.preview.spine_adapter.subprocess.run", fake_run)
    runner = SpineQueryRunner(str(tmp_path / "SpineViewerCLI.exe"))
    first = runner.query_skins(str(tmp_path / "hero.skel"), str(tmp_path / "hero.atlas"))
    reordered = runner.query_skins(str(tmp_path / "hero.skel"), str(tmp_path / "hero.atlas"))
    changed = runner.query_skins(str(tmp_path / "hero.skel"), str(tmp_path / "hero.atlas"))

    assert first.attachment_fingerprints["base"] == reordered.attachment_fingerprints["base"]
    assert first.attachment_fingerprints["base"] != changed.attachment_fingerprints["base"]
    assert first.attachment_fingerprint_kind == "attachment_set"


def test_query_skins_labels_source_skin_fallback_when_attachments_are_unavailable(monkeypatch, tmp_path):
    def fake_run(_command, **_kwargs):
        return SimpleNamespace(returncode=0, stdout="Skin:\nbase\n", stderr="")

    monkeypatch.setattr("app.features.preview.spine_adapter.subprocess.run", fake_run)
    runner = SpineQueryRunner(str(tmp_path / "SpineViewerCLI.exe"))

    result = runner.query_skins(str(tmp_path / "hero.skel"), str(tmp_path / "hero.atlas"))

    assert result.attachment_fingerprints == {}
    assert result.identity_fingerprints["base"]
    assert result.attachment_fingerprint_kind == "source_skin_identity"
    assert "attachment" in result.diagnostic.lower()


def test_query_skins_preserves_timeout_as_failure(monkeypatch, tmp_path):
    def fake_run(_command, **_kwargs):
        raise pytest.importorskip("subprocess").TimeoutExpired("query", 15)

    monkeypatch.setattr("app.features.preview.spine_adapter.subprocess.run", fake_run)
    result = SpineQueryRunner("SpineViewerCLI.exe").query_skins(
        str(tmp_path / "hero.skel"), str(tmp_path / "hero.atlas")
    )

    assert result.timed_out
    assert "timed out" in result.error
