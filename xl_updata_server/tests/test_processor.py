from datetime import datetime, timezone
from pathlib import Path
import json

import pytest

from server_app.cdn import CategoryInfo, UpdateInfo, VersionCatalog
from server_app.character_card_exporter import CardExportReport
from server_app.config import ServerConfig
from server_app.processor import ProductionUpdateProcessor


def _config(data_dir: Path) -> ServerConfig:
    return ServerConfig(
        config_path=data_dir / "config.toml",
        data_dir=data_dir,
        cdn_base="https://example.com",
        timezone_name="Asia/Shanghai",
        normal_interval_seconds=3600,
        burst_interval_seconds=60,
        burst_duration_seconds=1200,
        burst_anchor=datetime(2026, 9, 18, 10, 0, tzinfo=timezone.utc),
        selected_categories=("Arts", "Data"),
        java_bin="java",
        unluac_jar=data_dir / "unluac.jar",
        unluac_opmap=data_dir / "opmap",
        character_card_font=None,
        node_bin="node",
        assetbundle_key=b"yunguihaowan1234",
    )


def _make_card_file(staging: Path, character_id: str) -> Path:
    path = staging / "character_cards" / f"{character_id}_A_角色档案_长图.png"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(b"png")
    return path


def _patch_cdn(processor: ProductionUpdateProcessor, monkeypatch, timestamp: int = 2):
    monkeypatch.setattr(
        processor.client, "fetch_update_info", lambda: UpdateInfo(timestamp=timestamp, file="v.json")
    )
    monkeypatch.setattr(
        processor.client,
        "fetch_version_catalog",
        lambda _file: VersionCatalog(
            timestamp=timestamp,
            categories=(CategoryInfo(name="Data", hash="datahash", size=10, version=1),),
        ),
    )
    monkeypatch.setattr(processor.client, "download_category_catalog", lambda *args, **kwargs: Path("catalog"))
    monkeypatch.setattr(processor.client, "download_bundle", lambda hash, dest: Path(dest))


def _patch_lua_pipeline(monkeypatch):
    from server_app.catalog import AssetRef, BundleRef, CatalogIndex

    catalog = CatalogIndex(
        bundles=(BundleRef(name="lua.bundle", hash="hash", size=1, deps=()),),
        assets=(AssetRef(name="BaseCard.lua.bytes", bundle_index=0, container="Assets/Lua"),),
    )
    monkeypatch.setattr("server_app.processor.read_catalog", lambda _path, _key: catalog)
    monkeypatch.setattr("server_app.processor.extract_lua_files", lambda _paths, _out: None)
    monkeypatch.setattr(
        "server_app.processor.decode_lua_directory",
        lambda *args, **kwargs: type("R", (), {"success": 2, "failed": 0})(),
    )


def test_processor_renders_only_new_characters(tmp_path, monkeypatch):
    data_dir = tmp_path / "data"
    data_dir.mkdir()
    current = data_dir / "character_data" / "current.json"
    current.parent.mkdir(parents=True, exist_ok=True)
    current.write_text(
        '{"version": 1, "characters": {"10001": {"name": "A"}, "10002": {"name": "B"}}}', encoding="utf-8"
    )

    processor = ProductionUpdateProcessor(_config(data_dir))
    _patch_cdn(processor, monkeypatch)
    _patch_lua_pipeline(monkeypatch)

    captured_ids = {}

    def fake_export(chars, out_dir, font, only_character_ids=None, **_kwargs):
        captured_ids["only"] = only_character_ids
        path = _make_card_file(Path(out_dir).parent, "10003")
        return CardExportReport(
            seen=len(only_character_ids) if only_character_ids else len(chars),
            created=1,
            warned=0,
            failed=0,
            outputs=(path,),
            warnings=(),
            failures=(),
            character_outputs=(("10003", path),),
        )

    monkeypatch.setattr("server_app.processor.export_character_cards", fake_export)
    monkeypatch.setattr(
        "server_app.processor.parse_character_snapshot",
        lambda _dir: {
            "characters": {
                "10001": {"name": "A"},
                "10002": {"name": "B2"},
                "10003": {"name": "C"},
            }
        },
    )

    staging = tmp_path / "staging"
    result = processor(staging)

    assert result.processed is True
    assert result.card_count == 1
    assert captured_ids["only"] == {"10002", "10003"}  # 新增 ∪ 变更（10002 的 name B→B2）
    assert result.updated_character_count == 1

    outbox = data_dir / "outbox" / "2"
    assert outbox.is_dir()
    assert (outbox / "10003_A_角色档案_长图.png").is_file()
    manifest = __import__("json").loads((outbox / "manifest.json").read_text(encoding="utf-8"))
    assert manifest["version"] == 2
    assert [item["character_id"] for item in manifest["characters"]] == ["10003"]  # 变更角色不进 outbox


def test_processor_no_new_characters_skips_outbox(tmp_path, monkeypatch):
    data_dir = tmp_path / "data"
    data_dir.mkdir()
    current = data_dir / "character_data" / "current.json"
    current.parent.mkdir(parents=True, exist_ok=True)
    current.write_text(
        '{"version": 1, "characters": {"10001": {"name": "A"}}}', encoding="utf-8"
    )

    processor = ProductionUpdateProcessor(_config(data_dir))
    _patch_cdn(processor, monkeypatch, timestamp=2)
    _patch_lua_pipeline(monkeypatch)

    captured = {}

    def fake_export(chars, out_dir, font, only_character_ids=None, **_kwargs):
        captured["only"] = only_character_ids
        return CardExportReport(
            seen=0, created=0, warned=0, failed=0, outputs=(), warnings=(), failures=()
        )

    monkeypatch.setattr("server_app.processor.export_character_cards", fake_export)
    monkeypatch.setattr(
        "server_app.processor.parse_character_snapshot",
        lambda _dir: {"characters": {"10001": {"name": "A2"}}},
    )

    staging = tmp_path / "staging"
    result = processor(staging)

    assert result.processed is True
    assert result.card_count == 0
    assert captured["only"] == {"10001"}  # 变更角色进入重渲集合
    assert result.updated_character_count == 1
    assert not (data_dir / "outbox" / "2").exists()  # 但变更不推送 outbox
    assert (staging / "character_data" / "current.json").is_file()
    assert (staging / "manifest.json").is_file()
    manifest = __import__("json").loads((staging / "manifest.json").read_text(encoding="utf-8"))
    assert manifest["updated_characters_count"] == 1
    assert manifest["new_characters_count"] == 0


def test_processor_cleans_bundles_and_work_before_return(tmp_path, monkeypatch):
    data_dir = tmp_path / "data"
    data_dir.mkdir()

    processor = ProductionUpdateProcessor(_config(data_dir))
    _patch_cdn(processor, monkeypatch, timestamp=2)
    _patch_lua_pipeline(monkeypatch)

    def fake_export(chars, out_dir, font, only_character_ids=None, **_kwargs):
        path = _make_card_file(Path(out_dir).parent, "10001")
        return CardExportReport(
            seen=1, created=1, warned=0, failed=0, outputs=(path,), warnings=(), failures=(),
            character_outputs=(("10001", path),),
        )

    monkeypatch.setattr("server_app.processor.export_character_cards", fake_export)
    monkeypatch.setattr(
        "server_app.processor.parse_character_snapshot",
        lambda _dir: {"characters": {"10001": {"name": "A"}}},
    )

    staging = tmp_path / "staging"
    (staging / "bundles" / "Data").mkdir(parents=True)
    (staging / ".work" / "lua-decoded").mkdir(parents=True)

    result = processor(staging)

    assert result.processed is True
    assert result.card_count == 1
    assert not (staging / "bundles").exists()
    assert not (staging / ".work").exists()
    assert not (data_dir / "outbox" / "2").exists()
    manifest = __import__("json").loads((staging / "manifest.json").read_text(encoding="utf-8"))
    assert manifest["baseline"] is True


def test_processor_first_run_renders_all_but_skips_outbox(tmp_path, monkeypatch):
    data_dir = tmp_path / "data"
    data_dir.mkdir()

    processor = ProductionUpdateProcessor(_config(data_dir))
    _patch_cdn(processor, monkeypatch, timestamp=2)
    _patch_lua_pipeline(monkeypatch)

    captured = {}

    def fake_export(chars, out_dir, font, only_character_ids=None, **_kwargs):
        captured["only"] = only_character_ids
        path = _make_card_file(Path(out_dir).parent, "10001")
        return CardExportReport(
            seen=len(chars), created=2, warned=0, failed=0, outputs=(path,),
            warnings=(), failures=(), character_outputs=(("10001", path),),
        )

    monkeypatch.setattr("server_app.processor.export_character_cards", fake_export)
    monkeypatch.setattr(
        "server_app.processor.parse_character_snapshot",
        lambda _dir: {"characters": {"10001": {"name": "A"}, "10002": {"name": "B"}}},
    )

    staging = tmp_path / "staging"
    result = processor(staging)

    assert result.processed is True
    assert result.card_count == 2
    assert captured["only"] is None
    assert (staging / "character_cards").is_dir()
    assert not (data_dir / "outbox" / "2").exists()
    manifest = __import__("json").loads((staging / "manifest.json").read_text(encoding="utf-8"))
    assert manifest["baseline"] is True
    assert manifest["new_characters_count"] == 2


def test_processor_returns_early_when_version_unchanged(tmp_path, monkeypatch):
    data_dir = tmp_path / "data"
    data_dir.mkdir()
    pointer = data_dir / "current_version.json"
    pointer.write_text('{"version": 5}', encoding="utf-8")

    processor = ProductionUpdateProcessor(_config(data_dir))
    monkeypatch.setattr(processor.client, "fetch_update_info", lambda: UpdateInfo(timestamp=5, file="v.json"))

    result = processor(tmp_path / "unused")

    assert result.processed is False
    assert result.version_timestamp is None


def test_processor_skips_rendering_when_cards_disabled(tmp_path, monkeypatch):
    import dataclasses

    data_dir = tmp_path / "data"
    data_dir.mkdir()
    processor = ProductionUpdateProcessor(
        dataclasses.replace(_config(data_dir), render_cards=False)
    )
    _patch_cdn(processor, monkeypatch)
    _patch_lua_pipeline(monkeypatch)
    monkeypatch.setattr(
        "server_app.processor.parse_character_snapshot",
        lambda _dir: {"characters": {"10001": {"name": "A"}}},
    )

    def forbidden_export(*_args, **_kwargs):
        raise AssertionError("cards.enabled=false 时不应调用渲染")

    monkeypatch.setattr("server_app.processor.export_character_cards", forbidden_export)

    staging = tmp_path / "staging"
    result = processor(staging)

    assert result.processed is True
    assert result.card_count == 0
    assert not (staging / "character_cards").exists() or not list((staging / "character_cards").iterdir())
    assert not (data_dir / "outbox").exists()
    current = __import__("json").loads(
        (staging / "character_data" / "current.json").read_text(encoding="utf-8")
    )
    assert set(current["characters"]) == {"10001"}


def test_processor_writes_update_status(tmp_path, monkeypatch):
    import json as _json

    data_dir = tmp_path / "data"
    data_dir.mkdir()
    current = data_dir / "character_data" / "current.json"
    current.parent.mkdir(parents=True, exist_ok=True)
    current.write_text('{"version": 1, "characters": {"10001": {"name": "A"}}}', encoding="utf-8")

    processor = ProductionUpdateProcessor(_config(data_dir))
    _patch_cdn(processor, monkeypatch)
    _patch_lua_pipeline(monkeypatch)
    monkeypatch.setattr(
        "server_app.processor.parse_character_snapshot",
        lambda _dir: {"characters": {"10001": {"name": "A"}, "10002": {"name": "B"}}},
    )
    monkeypatch.setattr(
        "server_app.processor.export_character_cards",
        lambda *a, **k: CardExportReport(seen=1, created=1, warned=0, failed=0,
                                         outputs=(), warnings=(), failures=(),
                                         character_outputs=()),
    )

    result = processor(tmp_path / "staging")

    assert result.processed is True
    status = _json.loads((data_dir / "update_status.json").read_text(encoding="utf-8"))
    assert status["updating"] is False
    assert status["version"] == 2
    assert status["new_characters"] == 1
    assert status["error"] is None


def test_processor_writes_error_status_on_failure(tmp_path, monkeypatch):
    import json as _json

    data_dir = tmp_path / "data"
    data_dir.mkdir()
    processor = ProductionUpdateProcessor(_config(data_dir))
    _patch_cdn(processor, monkeypatch)
    monkeypatch.setattr("server_app.processor.read_catalog", lambda *_a, **_k: (_ for _ in ()).throw(RuntimeError("boom")))
    monkeypatch.setattr("server_app.processor.select_lua_assets", lambda _c: ())

    try:
        processor(tmp_path / "staging")
        raised = False
    except RuntimeError:
        raised = True

    assert raised
    status = _json.loads((data_dir / "update_status.json").read_text(encoding="utf-8"))
    assert status["updating"] is False
    assert status["error"] == "boom"


# ---------- 信息变更重渲（updated_characters） ----------


def _setup_changed_scene(tmp_path, monkeypatch, current_chars, parsed_chars, timestamp=2):
    data_dir = tmp_path / "data"
    data_dir.mkdir()
    current = data_dir / "character_data" / "current.json"
    current.parent.mkdir(parents=True, exist_ok=True)
    current.write_text(
        json.dumps({"version": 1, "characters": current_chars}, ensure_ascii=False),
        encoding="utf-8",
    )
    processor = ProductionUpdateProcessor(_config(data_dir))
    _patch_cdn(processor, monkeypatch, timestamp=timestamp)
    _patch_lua_pipeline(monkeypatch)
    monkeypatch.setattr(
        "server_app.processor.parse_character_snapshot",
        lambda _dir: {"characters": parsed_chars},
    )
    return processor, data_dir


def test_changed_only_rerenders_without_outbox(tmp_path, monkeypatch):
    """仅变更：重渲变更角色，但不写 outbox（硬约束）。"""
    processor, data_dir = _setup_changed_scene(
        tmp_path, monkeypatch,
        {"10001": {"name": "A", "desc": "旧"}},
        {"10001": {"name": "A", "desc": "新"}},
    )
    captured = {}

    def fake_export(chars, out_dir, font, only_character_ids=None, **_kwargs):
        captured["only"] = only_character_ids
        return CardExportReport(seen=1, created=1, warned=0, failed=0,
                                outputs=(), warnings=(), failures=(), character_outputs=())

    monkeypatch.setattr("server_app.processor.export_character_cards", fake_export)
    result = processor(tmp_path / "staging")

    assert captured["only"] == {"10001"}
    assert result.new_character_count == 0
    assert result.updated_character_count == 1
    assert not (data_dir / "outbox").exists()


def test_changed_card_overwrites_old_file(tmp_path, monkeypatch):
    """变更角色的新长图覆盖同名旧文件（文件名规则不变）。"""
    processor, _ = _setup_changed_scene(
        tmp_path, monkeypatch,
        {"10001": {"name": "A"}},
        {"10001": {"name": "A", "story": "补充了档案"}},
    )
    staging = tmp_path / "staging"
    card_path = staging / "character_cards" / "10001_A_角色档案_长图.png"
    card_path.parent.mkdir(parents=True, exist_ok=True)
    card_path.write_bytes(b"old-png")

    def fake_export(chars, out_dir, font, only_character_ids=None, **_kwargs):
        assert str(out_dir).endswith("character_cards")
        card_path.write_bytes(b"new-png")  # 同名覆盖
        return CardExportReport(seen=1, created=1, warned=0, failed=0,
                                outputs=(card_path,), warnings=(), failures=(),
                                character_outputs=(("10001", card_path),))

    monkeypatch.setattr("server_app.processor.export_character_cards", fake_export)
    result = processor(staging)

    assert result.updated_character_count == 1
    assert card_path.read_bytes() == b"new-png"


def test_new_plus_changed_union_rendered_but_outbox_only_new(tmp_path, monkeypatch):
    """新增 ∪ 变更一起渲染；outbox 只装新增。"""
    processor, data_dir = _setup_changed_scene(
        tmp_path, monkeypatch,
        {"10001": {"name": "A"}},
        {"10001": {"name": "A2"}, "10002": {"name": "B"}},
    )
    captured = {}
    new_card = _make_card_file(tmp_path / "staging", "10002")

    def fake_export(chars, out_dir, font, only_character_ids=None, **_kwargs):
        captured["only"] = only_character_ids
        return CardExportReport(seen=2, created=2, warned=0, failed=0,
                                outputs=(new_card,), warnings=(), failures=(),
                                character_outputs=(("10001", new_card), ("10002", new_card)))

    monkeypatch.setattr("server_app.processor.export_character_cards", fake_export)
    result = processor(tmp_path / "staging")

    assert captured["only"] == {"10001", "10002"}
    assert result.new_character_count == 1
    assert result.updated_character_count == 1
    outbox = data_dir / "outbox" / "2"
    manifest = json.loads((outbox / "manifest.json").read_text(encoding="utf-8"))
    assert [item["character_id"] for item in manifest["characters"]] == ["10002"]
    staging_manifest = json.loads(
        (tmp_path / "staging" / "manifest.json").read_text(encoding="utf-8")
    )
    assert staging_manifest["new_characters_count"] == 1
    assert staging_manifest["updated_characters_count"] == 1


def test_identical_characters_no_updates(tmp_path, monkeypatch):
    """数据全等：无新增无变更，不重渲、无 outbox。"""
    processor, data_dir = _setup_changed_scene(
        tmp_path, monkeypatch,
        {"10001": {"name": "A", "tags": ["x", "y"]}},
        {"10001": {"name": "A", "tags": ["x", "y"]}},
    )
    captured = {}

    def fake_export(chars, out_dir, font, only_character_ids=None, **_kwargs):
        captured["only"] = only_character_ids
        return CardExportReport(seen=0, created=0, warned=0, failed=0,
                                outputs=(), warnings=(), failures=())

    monkeypatch.setattr("server_app.processor.export_character_cards", fake_export)
    result = processor(tmp_path / "staging")

    assert captured["only"] == set()
    assert result.new_character_count == 0
    assert result.updated_character_count == 0
    assert not (data_dir / "outbox").exists()


def test_key_order_difference_is_not_a_change(tmp_path, monkeypatch):
    """字段顺序不同但内容全等 → 规范化 JSON 比较视为无变更。"""
    processor, _ = _setup_changed_scene(
        tmp_path, monkeypatch,
        {"10001": {"name": "A", "desc": "d", "star": 5}},
        {"10001": {"star": 5, "name": "A", "desc": "d"}},
    )
    monkeypatch.setattr(
        "server_app.processor.export_character_cards",
        lambda *a, **k: CardExportReport(seen=0, created=0, warned=0, failed=0,
                                         outputs=(), warnings=(), failures=()),
    )
    result = processor(tmp_path / "staging")
    assert result.updated_character_count == 0


def test_baseline_produces_no_updates(tmp_path, monkeypatch):
    """首跑无基线：全部视为新增，不产生变更。"""
    data_dir = tmp_path / "data"
    data_dir.mkdir()
    processor = ProductionUpdateProcessor(_config(data_dir))
    _patch_cdn(processor, monkeypatch, timestamp=2)
    _patch_lua_pipeline(monkeypatch)
    monkeypatch.setattr(
        "server_app.processor.parse_character_snapshot",
        lambda _dir: {"characters": {"10001": {"name": "A"}, "10002": {"name": "B"}}},
    )
    monkeypatch.setattr(
        "server_app.processor.export_character_cards",
        lambda *a, **k: CardExportReport(seen=2, created=2, warned=0, failed=0,
                                         outputs=(), warnings=(), failures=()),
    )
    result = processor(tmp_path / "staging")
    assert result.new_character_count == 2
    assert result.updated_character_count == 0
    manifest = json.loads(
        (tmp_path / "staging" / "manifest.json").read_text(encoding="utf-8")
    )
    assert manifest["updated_characters_count"] == 0
