import json
import subprocess
from pathlib import Path

import pytest

from server_app.character_card_renderer import (
    CharacterCardRenderResult,
    render_character_card,
    render_character_cards_batch,
)
from server_app.character_cards import normalize_character_record

RENDERER_DIR = Path(__file__).resolve().parent.parent / "renderer"
NODE_MODULES_EXISTS = (RENDERER_DIR / "node_modules").is_dir()


def test_render_character_cards_batch_calls_node_with_payload(tmp_path, monkeypatch):
    records = (
        normalize_character_record("10001", {"name": "A"}),
        normalize_character_record("10002", {"name": "B"}),
    )
    captured = {}

    def fake_run(command, **kwargs):
        captured["command"] = command
        captured["cwd"] = kwargs.get("cwd")
        payload_path = Path(command[-1])
        captured["payload"] = json.loads(payload_path.read_text(encoding="utf-8"))
        return subprocess.CompletedProcess(
            args=command,
            returncode=0,
            stdout=json.dumps(
                {
                    "results": [
                        {"id": "10001", "ok": True, "path": str(tmp_path / "10001_A.png"), "height": 1200, "warnings": []},
                        {"id": "10002", "ok": True, "path": str(tmp_path / "10002_B.png"), "height": 1300, "warnings": ["w1"]},
                    ]
                }
            ),
            stderr="",
        )

    monkeypatch.setattr("server_app.character_card_renderer.subprocess.run", fake_run)
    results = render_character_cards_batch(records, tmp_path)

    assert len(results) == 2
    assert results[0].character_id == "10001"
    assert results[0].height == 1200
    assert results[0].warnings == ()
    assert results[1].warnings == ("w1",)
    assert captured["command"][0] == "node"
    assert captured["command"][1] == "--max-old-space-size=512"
    assert captured["command"][2].endswith("render_batch.js")
    assert captured["cwd"].name == "renderer"
    assert captured["payload"]["ids"] == ["10001", "10002"]
    assert set(captured["payload"]["characters"].keys()) == {"10001", "10002"}


def test_render_character_cards_batch_reports_per_character_failure(tmp_path, monkeypatch):
    records = (
        normalize_character_record("10001", {"name": "A"}),
    )

    def fake_run(command, **kwargs):
        return subprocess.CompletedProcess(
            args=command,
            returncode=1,
            stdout=json.dumps({"results": [{"id": "10001", "ok": False, "error": "boom"}]}),
            stderr="",
        )

    monkeypatch.setattr("server_app.character_card_renderer.subprocess.run", fake_run)
    results = render_character_cards_batch(records, tmp_path)

    assert results[0].error == "boom"
    assert results[0].height is None


def test_render_character_cards_batch_raises_file_not_found_for_missing_node(tmp_path, monkeypatch):
    records = (normalize_character_record("10001", {"name": "A"}),)

    def fake_run(_command, **_kwargs):
        raise FileNotFoundError("node not found")

    monkeypatch.setattr("server_app.character_card_renderer.subprocess.run", fake_run)
    with pytest.raises(FileNotFoundError):
        render_character_cards_batch(records, tmp_path, node_bin="/no/such/node")


def test_render_character_cards_batch_raises_file_not_found_for_missing_script(tmp_path):
    records = (normalize_character_record("10001", {"name": "A"}),)
    with pytest.raises(FileNotFoundError):
        render_character_cards_batch(records, tmp_path, renderer_dir=tmp_path / "no_renderer")


def test_render_character_card_keeps_single_record_contract(tmp_path, monkeypatch):
    record = normalize_character_record("10001", {"name": "测试/Test"})

    def fake_run(command, **kwargs):
        payload_path = Path(command[-1])
        payload = json.loads(payload_path.read_text(encoding="utf-8"))
        output_path = str(Path(kwargs["cwd"]).parent / payload["name_template"])
        return subprocess.CompletedProcess(
            args=command,
            returncode=0,
            stdout=json.dumps(
                {"results": [{"id": "10001", "ok": True, "path": output_path, "height": 1500, "warnings": []}]}
            ),
            stderr="",
        )

    monkeypatch.setattr("server_app.character_card_renderer.subprocess.run", fake_run)
    height, warnings = render_character_card(record, tmp_path / "10001_测试_角色档案_长图.png")

    assert height == 1500
    assert warnings == ()


@pytest.mark.skipif(not NODE_MODULES_EXISTS, reason="renderer/node_modules not present")
def test_render_batch_real_smoke(tmp_path):
    """真实 Node 渲染 smoke 测试：用 tests/fixtures 角色 JSON 直接调用 render_batch.js。"""

    fixture = Path(__file__).resolve().parent / "fixtures" / "smoke_character.json"
    payload = json.loads(fixture.read_text(encoding="utf-8"))
    payload["out_dir"] = str(tmp_path)
    payload_path = tmp_path / "payload.json"
    payload_path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")

    completed = subprocess.run(
        ["node", "--max-old-space-size=512", str(RENDERER_DIR / "render_batch.js"), str(payload_path)],
        cwd=RENDERER_DIR,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=120,
    )

    report = json.loads(completed.stdout)
    result = report["results"][0]
    assert result["ok"] is True
    output_path = Path(result["path"])
    assert output_path.is_file()
    assert output_path.stat().st_size > 10 * 1024
    data = output_path.read_bytes()
    assert data.startswith(b"\x89PNG\r\n\x1a\n")
    assert isinstance(result["height"], int)
    assert result["height"] > 1000
