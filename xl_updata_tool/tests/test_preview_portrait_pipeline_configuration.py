from pathlib import Path

from app.features.preview.portrait_config.lua_config import load_eventcover_roots
from app.features.preview.portrait_config.model import PortraitScene
from app.features.preview.portrait_config.asset_index import AssetIndex
from app.features.preview.portrait_config.builder import build_portrait_scenes
from app.features.preview.portrait_config.lua_config import PortraitRoot


def test_scene_keeps_game_display_position_and_config_usage():
    scene = PortraitScene(
        game_version="402", version_timestamp="402", entry_config_id="12001001",
        character_id="10000100", skin_id="12001001", prefab=None,
        source_bundles=(), parts=(), display_position=(395.0, -897.0),
        config_metadata=(("usage", "portrait"), ("default_expression", "motion_stander")),
    )
    restored = PortraitScene.from_dict(scene.to_dict())
    assert restored.display_position == (395.0, -897.0)
    assert dict(restored.config_metadata)["usage"] == "portrait"
    assert not any(d.code == "unknown_scene_field" for d in restored.diagnostics)


def test_known_japan_only_missing_prefab_is_not_a_cn_export_failure():
    root = PortraitRoot("12001991", 402, "cardspine_10099_1.prefab", None,
                        (("card_id", "10000199"),), "BaseFashion.lua:1")
    scene = build_portrait_scenes(402, [root], AssetIndex(402, ()))[0]
    assert dict(scene.config_metadata)["export_status"] == "not_applicable"
    assert dict(scene.config_metadata)["configuration_evidence"] == "BaseFashion.lua:1"
    assert dict(scene.config_metadata)["configured_prefab"] == "cardspine_10099_1.prefab"
    assert not any(d.severity == "error" for d in scene.validate())


def test_event_sequence_has_four_independently_paired_stage_roots(tmp_path):
    folder = tmp_path / "402"
    folder.mkdir()
    (folder / "BaseActivityStageConfig.lua").write_text(
        'BaseActivityStageConfig = { [1] = { id=1, show_id=5, '
        'bg_main="Assets/Art/Models/UI_spine/prefab/Eventcovers/eventcovers_0005.prefab" } }',
        encoding="utf-8",
    )
    (folder / "Activity5DungeonWindow.lua").write_text(
        'function Activity5DungeonWindow.LoadBg()\n UIUtil.SetHolderCenter(holder)\nend\n', encoding="utf-8")
    paths = ",".join(
        f'[{i}]="Assets/Art/Models/UI_spine/prefab/Eventcovers/eventcovers_0005_{stage}.prefab"'
        for i, stage in enumerate((4, 1, 2, 3), 1)
    )
    (folder / "Activity5EntranceAnimWindow.lua").write_text(
        'local PATH_LOOKUP={' + paths + '}\n'
        'local function PlayDailyEntranceAnim(num, endNum)\n'
        ' local path = PATH_LOOKUP[num]\n local o = ResourceManager.Instantiate(path)\n'
        ' UIUtil.SetObjectToUI(o, holder)\n'
        ' PlayDailyEntranceAnim(num + 1, endNum)\n holder:Dispose()\nend\n', encoding="utf-8")
    roots = load_eventcover_roots(tmp_path, 402)
    assert [r.skin_id for r in roots] == [f"eventcovers_0005_stage_{i:02d}" for i in range(1, 5)]
    assert [Path(r.prefab_name).stem for r in roots] == [
        "eventcovers_0005_4", "eventcovers_0005_1", "eventcovers_0005_2", "eventcovers_0005_3"]
    assert [dict(r.metadata)["stage_index"] for r in roots] == ["1", "2", "3", "4"]
    assert all(r.event_runtime.purpose == "entrance_stage" for r in roots)
