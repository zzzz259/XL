from pathlib import Path

from server_app.character_card_exporter import export_character_cards
from server_app.character_card_renderer import CharacterCardRenderResult


def _make_batch(outputs):
    def batch(records, out_dir, node_bin="node", renderer_dir=None):
        results = []
        for record in records:
            if record.character_id in outputs:
                path = outputs[record.character_id]
                if path is None:
                    results.append(
                        CharacterCardRenderResult(
                            character_id=record.character_id,
                            output_path=out_dir / f"{record.character_id}_fail.png",
                            height=None,
                            warnings=(),
                            error="render failed",
                        )
                    )
                else:
                    results.append(
                        CharacterCardRenderResult(
                            character_id=record.character_id,
                            output_path=Path(path),
                            height=1000,
                            warnings=(),
                            error=None,
                        )
                    )
        return tuple(results)

    return batch


def test_export_character_cards_writes_one_card_per_character_and_reports_counts(tmp_path, monkeypatch):
    out_dir = tmp_path / "character_cards"
    monkeypatch.setattr(
        "server_app.character_card_exporter.render_character_cards_batch",
        _make_batch(
            {
                "10001": out_dir / "10001_A_角色档案_长图.png",
                "10002": out_dir / "10002_B_角色档案_长图.png",
            }
        ),
    )

    report = export_character_cards(
        {"10002": {"name": "B"}, "10001": {"name": "A"}},
        out_dir,
    )

    assert report.seen == 2
    assert report.created == 2
    assert report.failed == 0
    assert len(report.outputs) == 2
    assert all(path.suffix == ".png" for path in report.outputs)


def test_export_character_cards_continues_after_one_record_failure(tmp_path, monkeypatch):
    monkeypatch.setattr(
        "server_app.character_card_exporter.render_character_cards_batch",
        _make_batch({"10001": None}),
    )
    report = export_character_cards({"10001": {"name": "A"}}, tmp_path / "cards")

    assert report.seen == 1
    assert report.created == 0
    assert report.failed == 1
    assert report.failures[0].character_id == "10001"


def test_export_character_cards_filters_to_only_requested_ids(tmp_path, monkeypatch):
    out_dir = tmp_path / "character_cards"
    monkeypatch.setattr(
        "server_app.character_card_exporter.render_character_cards_batch",
        _make_batch({"10001": out_dir / "10001_A_角色档案_长图.png"}),
    )

    report = export_character_cards(
        {"10002": {"name": "B"}, "10001": {"name": "A"}},
        out_dir,
        only_character_ids={"10001"},
    )

    assert report.seen == 1
    assert report.created == 1
    assert report.character_outputs[0][0] == "10001"
    assert report.character_outputs[0][1].name.startswith("10001_")


def test_export_character_cards_passes_node_bin_and_renderer_dir(tmp_path, monkeypatch):
    captured = {}

    def capture_batch(records, out_dir, node_bin="node", renderer_dir=None):
        captured["node_bin"] = node_bin
        captured["renderer_dir"] = renderer_dir
        return tuple(
            CharacterCardRenderResult(
                character_id=record.character_id,
                output_path=out_dir / f"{record.character_id}.png",
                height=100,
                warnings=(),
                error=None,
            )
            for record in records
        )

    monkeypatch.setattr(
        "server_app.character_card_exporter.render_character_cards_batch", capture_batch
    )
    export_character_cards(
        {"10001": {"name": "A"}},
        tmp_path / "cards",
        node_bin="/usr/bin/node",
        renderer_dir=tmp_path / "renderer",
    )

    assert captured["node_bin"] == "/usr/bin/node"
    assert captured["renderer_dir"] == tmp_path / "renderer"
