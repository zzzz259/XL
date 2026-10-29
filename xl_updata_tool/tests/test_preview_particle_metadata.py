from app.features.preview.portrait_config import model
from app.features.preview.portrait_config.model import (
    AssetRef,
    PortraitScene,
    ScenePart,
    InteractionEvent,
    AnimationStep,
)


def test_schema8_preserves_particle_and_tracks():
    state = model.ParticleState(AssetRef("a", 2), AssetRef("b", 3), AssetRef("c", 4), "additive_v1", '{"version":1}')
    scene = PortraitScene(
        "",
        "1",
        "",
        "",
        "",
        None,
        (),
        (ScenePart("p", AssetRef("a", 1), "Fire", particle=state),),
        interactions=(
            InteractionEvent(
                "click", track=1, sequence=(AnimationStep("touch", False),), gate="idle", evidence=("Lua:1",)
            ),
        ),
    )
    loaded = PortraitScene.from_dict(scene.to_dict())
    assert loaded == scene
    assert loaded.schema_version == 8


def test_old_scene_requires_explicit_upgrade():
    scene = PortraitScene("", "1", "", "", "", None, (), ())
    for version in (6, 7):
        data = scene.to_dict()
        data["schema_version"] = version
        loaded = PortraitScene.from_dict(data)
        assert "portrait_engineering_upgrade_required" in {d.code for d in loaded.diagnostics}


def test_supported_profile_without_valid_config_cannot_pass_validation():
    state = model.ParticleState(AssetRef("a", 2), AssetRef("a", 3), AssetRef("a", 4), "additive_v1", '{"version":1}')
    part = ScenePart("p", AssetRef("a", 1), "Fire", particle=state)
    scene = PortraitScene(
        "",
        "1",
        "",
        "",
        "",
        None,
        (),
        (part,),
        available_assets=tuple(AssetRef("a", i) for i in range(1, 5)),
        prefab_container="target.prefab",
    )
    assert "invalid_particle_config" in {d.code for d in scene.validate()}
