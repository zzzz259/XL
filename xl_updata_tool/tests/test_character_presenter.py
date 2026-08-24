import csv

from app.features.characters.presenter import build_character_detail_html, export_characters_csv


def test_build_character_detail_html_keeps_display_fields_and_line_breaks():
    name, html = build_character_detail_html({
        "name": "角色甲/备用名",
        "description": "第一行\n第二行",
        "crt": "1250",
        "normal_skill": "技能说明",
    })

    assert name == "角色甲"
    assert "第一行<br/>第二行" in html
    assert "12%" in html
    assert "技能说明" in html


def test_export_characters_csv_filters_and_preserves_columns(tmp_path):
    output = tmp_path / "characters.csv"
    count = export_characters_csv(str(output), {
        "valid": {
            "raw_id": 80100001,
            "name": "角色甲/备用名",
            "description": "第一行\n第二行",
            "normal_skill": "技能\n说明",
        },
        "outside": {"raw_id": 80110000, "name": "过滤"},
    })

    assert count == 1
    with output.open(encoding="utf-8-sig", newline="") as handle:
        rows = list(csv.reader(handle))
    assert rows[0][0:2] == ["ID", "Name"]
    assert rows[1][0:2] == ["80100001", "角色甲"]
    assert rows[1][9] == "第一行 第二行"
    assert rows[1][25] == "技能 | 说明"
