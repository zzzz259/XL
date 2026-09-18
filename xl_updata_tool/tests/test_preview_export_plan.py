from dataclasses import FrozenInstanceError, replace

import pytest

from app.features.preview.export_plan import (
    ExportSettings,
    SkinExportJob,
    build_default_export_plan,
    build_export_plan,
    build_spine_export_command,
)
from app.features.preview.resource_model import SpineSkinRecord, skin_key
from app.features.preview.character_names import CharacterNameResolver
from app.features.preview.export_presets import default_export_settings


def ready_record(
    *,
    character_id="10080",
    skin_name="holiday",
    attachment_fingerprint="abc",
    status="ready",
    resource_family="cardspine",
    source_suffix="4",
):
    return SpineSkinRecord(
        character_id=character_id,
        source_skel=f"assets/cardspine_{character_id}_{source_suffix}.skel",
        atlas_path=f"assets/cardspine_{character_id}_{source_suffix}.atlas",
        skin_name=skin_name,
        attachment_fingerprint=attachment_fingerprint,
        display_name=skin_name,
        status=status,
        resource_family=resource_family,
    )


def test_export_settings_have_explicit_immutable_defaults():
    settings = ExportSettings()

    assert settings.animation == "idle"
    assert settings.static is True
    assert settings.scale == 4
    assert settings.max_resolution == 8192
    assert settings.margin == 0
    assert settings.transparent is True
    assert settings.pma is True
    assert settings.format == "Png"
    assert settings.fps == 1

    with pytest.raises(FrozenInstanceError):
        settings.scale = 2


def test_export_plan_uses_character_and_skin_identity(tmp_path):
    record = ready_record(character_id="10080", skin_name="holiday", attachment_fingerprint="abc")

    plan = build_export_plan((record,), ExportSettings(), tmp_path)

    assert len(plan) == 1
    assert isinstance(plan[0], SkinExportJob)
    assert plan[0].record is record
    assert plan[0].settings == ExportSettings()
    assert plan[0].output_path == tmp_path / "10080" / "10080_角色立绘_4.png"


def test_default_settings_use_resource_specific_static_and_video_presets():
    card_png = default_export_settings("cardspine", "Png")
    card_mp4 = default_export_settings("cardspine", "Mp4", animation_duration=8.333334)
    battle_png = default_export_settings("battlespine", "Png")

    assert card_png == ExportSettings(
        animation="idle",
        static=True,
        scale=4,
        max_resolution=16000,
        margin=10,
        transparent=True,
        pma=True,
        format="Png",
        fps=1,
        duration=None,
        skins=("default",),
        background_color="#00000000",
        auto_border=True,
    )
    assert card_mp4.static is False
    assert card_mp4.scale == 1
    assert card_mp4.duration is None
    assert card_mp4.fps == 30
    assert card_mp4.transparent is False
    assert card_mp4.background_color == "#7f7f7f"
    assert battle_png.static is True
    assert battle_png.scale == 1
    assert battle_png.skins == ("motion_stander",)


def test_default_plan_expands_cardspine_to_png_and_mp4(tmp_path):
    character = ready_record(skin_name="default", source_suffix="4")
    background = replace(
        character,
        source_skel="assets/cardspine_10080_4_bg.skel",
        atlas_path="assets/cardspine_10080_4_bg.atlas",
        attachment_fingerprint="background",
    )

    plan = build_default_export_plan(
        ((character, background),),
        tmp_path / "character",
        animation_duration=8.333334,
    )

    assert [(job.settings.format, job.settings.static) for job in plan] == [
        ("Png", True),
        ("Mp4", False),
    ]
    assert plan[0].output_path == tmp_path / "character" / "10080" / "10080_角色立绘_4.png"
    assert plan[1].output_path == tmp_path / "character" / "10080" / "10080_角色立绘_4.mp4"
    assert plan[0].records == (character, background)
    assert plan[1].records == (character, background)
    assert plan[0].export_mode == "builtin"
    assert plan[1].export_mode == "builtin"


def test_default_plan_expands_battlespine_to_motion_stander_png_only(tmp_path):
    record = ready_record(
        resource_family="battlespine",
        skin_name="default",
        source_suffix="2",
    )

    plan = build_default_export_plan((record,), tmp_path / "character")

    assert len(plan) == 1
    assert plan[0].settings.format == "Png"
    assert plan[0].settings.static is True
    assert plan[0].settings.skins == ("motion_stander",)
    assert plan[0].output_path.suffix == ".png"


def test_default_cardspine_video_command_merges_parts_and_lets_cli_export_full_animation(tmp_path):
    character = ready_record(skin_name="default", source_suffix="4")
    background = replace(
        character,
        source_skel="assets/cardspine_10080_4_bg.skel",
        atlas_path="assets/cardspine_10080_4_bg.atlas",
        attachment_fingerprint="background",
    )
    job = build_default_export_plan(
        ((character, background),),
        tmp_path / "character",
        animation_duration=8.333334,
    )[1]

    command = build_spine_export_command(job, "SpineViewerCLI.exe")

    assert command[1] == "merge"
    assert command[2:4] == [character.source_skel, background.source_skel]
    assert command[command.index("-f") + 1] == "Mp4"
    assert command[command.index("--scale") + 1] == "1"
    assert "--duration" not in command
    assert command[command.index("--fps") + 1] == "30"
    assert command[command.index("--color") + 1] == "#7f7f7f"
    assert "--pma" in command


def test_export_plan_uses_resolved_role_name_in_flat_directory(tmp_path):
    data_dir = tmp_path / "output" / "character_data"
    data_dir.mkdir(parents=True)
    (data_dir / "characters_repository.json").write_text(
        '{"current_characters":{"10080":{"name":"菲尼斯/Finis"}},"history":{}}',
        encoding="utf-8",
    )
    resolver = CharacterNameResolver.from_output_root(tmp_path / "output")
    record = ready_record()

    plan = build_export_plan((record,), ExportSettings(), tmp_path / "character", resolver)

    assert plan[0].output_path == tmp_path / "character" / "10080_菲尼斯" / "10080_角色立绘_4.png"


def test_export_plan_adds_collision_suffix_without_overwriting(tmp_path):
    first = ready_record(attachment_fingerprint="first")
    second = ready_record(attachment_fingerprint="second")

    plan = build_export_plan((first, second), ExportSettings(), tmp_path)

    assert plan[0].output_path.name == "10080_角色立绘_4.png"
    assert plan[1].output_path.name == f"10080_角色立绘_4_{skin_key(second)[:8]}.png"


def test_export_command_uses_internal_skin_and_ui_settings(tmp_path):
    record = ready_record(character_id="10080", skin_name="skin_internal", attachment_fingerprint="abc")
    job = build_export_plan((record,), ExportSettings(animation="walk", static=False, scale=2, margin=8), tmp_path)[0]

    command = build_spine_export_command(job, "SpineViewerCLI.exe")

    assert command[command.index("--skins") + 1] == "skin_internal"
    assert command[command.index("--animations") + 1] == "walk"
    assert command[command.index("--scale") + 1] == "2"
    assert command[command.index("--margin") + 1] == "8"
    assert command[command.index("--max-resolution") + 1] == "8192"
    assert command[command.index("--fps") + 1] == "1"
    assert command[command.index("--color") + 1] == "#00000000"
    assert "--pma" in command


def test_export_command_does_not_pass_cli_reserved_default_skin(tmp_path):
    record = ready_record(skin_name="default")
    job = build_export_plan((record,), ExportSettings(), tmp_path)[0]

    command = build_spine_export_command(job, "SpineViewerCLI.exe")

    assert "--skins" not in command


def test_export_command_passes_selected_multiple_internal_skins(tmp_path):
    record = ready_record(skin_name="default")
    settings = ExportSettings(skins=("default", "motion_angry"), static=False, format="Mp4")
    job = build_export_plan((record,), settings, tmp_path)[0]

    command = build_spine_export_command(job, "SpineViewerCLI.exe")

    assert command[command.index("--animations") + 1] == "idle"
    assert command.count("--skins") == 1
    assert command[command.index("--skins") + 1] == "motion_angry"
    assert command[command.index("-f") + 1] == "Mp4"


def test_battlespine_preset_passes_motion_stander_to_cli(tmp_path):
    record = ready_record(resource_family="battlespine", skin_name="default")
    settings = ExportSettings(skins=("motion_stander",), static=True, format="Png")
    job = build_export_plan((record,), settings, tmp_path)[0]

    command = build_spine_export_command(job, "SpineViewerCLI.exe")

    assert command[command.index("--skins") + 1] == "motion_stander"


def test_static_export_is_one_frame_and_does_not_loop(tmp_path):
    record = ready_record()
    job = build_export_plan((record,), ExportSettings(), tmp_path)[0]

    command = build_spine_export_command(job, "SpineViewerCLI.exe")

    assert command[command.index("--duration") + 1] == "0"
    assert command[command.index("--fps") + 1] == "1"
    assert "--disable-track-loop" in command


def test_export_plan_skips_missing_character_and_non_ready_records(tmp_path):
    records = (
        ready_record(character_id=None),
        ready_record(status="invalid"),
        ready_record(character_id="10080", skin_name="ready"),
    )

    plan = build_export_plan(records, ExportSettings(), tmp_path)

    assert [job.record.skin_name for job in plan] == ["ready"]


def test_export_plan_never_creates_composite_job(tmp_path):
    record = ready_record(character_id="10080", skin_name="base", attachment_fingerprint="abc")

    assert all("composite" not in str(job.output_path) for job in build_export_plan((record,), ExportSettings(), tmp_path))


def test_export_plan_rejects_non_png_format_for_still_image_job(tmp_path):
    record = ready_record()

    with pytest.raises(ValueError, match="PNG, MP4, or GIF"):
        build_export_plan((record,), ExportSettings(format="Jpeg"), tmp_path)


def test_export_plan_keeps_background_part_in_one_job(tmp_path):
    character = ready_record(source_suffix="4")
    background = replace(
        character,
        source_skel="assets/cardspine_10080_4_bg.skel",
        atlas_path="assets/cardspine_10080_4_bg.atlas",
        attachment_fingerprint="background",
    )

    plan = build_export_plan(((character, background),), ExportSettings(), tmp_path)

    assert len(plan) == 1
    assert plan[0].record is character
    assert plan[0].records == (character, background)


def test_grouped_spine_export_uses_viewer_merge_for_all_parts(tmp_path):
    character = ready_record(source_suffix="4")
    background = replace(
        character,
        source_skel="assets/cardspine_10080_4_bg.skel",
        atlas_path="assets/cardspine_10080_4_bg.atlas",
        attachment_fingerprint="background",
    )

    job = build_export_plan(((character, background),), ExportSettings(), tmp_path)[0]
    command = build_spine_export_command(job, "SpineViewerCLI.exe")

    assert command[1] == "merge"
    assert command[2:4] == [character.source_skel, background.source_skel]
    atlas_args = [argument for argument in command if argument.startswith("--atlases=")]
    assert len(atlas_args) == 2
    assert atlas_args == [
        f"--atlases={character.atlas_path}",
        f"--atlases={background.atlas_path}",
    ]
    assert command[command.index("--animations") + 1] == "idle/idle"
    assert "--disable-track-loop" not in command
    assert "export" not in command


def test_grouped_export_deduplicates_internal_skin_records_per_source(tmp_path):
    character = ready_record(source_suffix="4", skin_name="default")
    character_motion = replace(character, skin_name="motion_angry")
    background = replace(
        character,
        source_skel="assets/cardspine_10080_4_bg.skel",
        atlas_path="assets/cardspine_10080_4_bg.atlas",
        attachment_fingerprint="background",
    )

    job = build_export_plan(
        ((character, character_motion, background),),
        ExportSettings(),
        tmp_path,
    )[0]
    command = build_spine_export_command(job, "SpineViewerCLI.exe")

    assert job.records == (character, background)
    assert command[2:4] == [character.source_skel, background.source_skel]
    assert command[command.index("--animations") + 1] == "idle/idle"


def test_export_plan_sanitizes_skin_filename_without_changing_internal_identity(tmp_path):
    record = ready_record(skin_name="holiday:night")

    job = build_export_plan((record,), ExportSettings(), tmp_path)[0]

    assert job.record.skin_name == "holiday:night"
    assert job.output_path.name == "10080_角色立绘_4.png"


@pytest.mark.parametrize(
    "reserved_name",
    [
        "CON",
        "PRN",
        "AUX",
        "NUL",
        *(f"COM{index}" for index in range(1, 10)),
        *(f"LPT{index}" for index in range(1, 10)),
    ],
)
def test_export_plan_sanitizes_windows_reserved_output_components(tmp_path, reserved_name):
    record = ready_record(character_id=reserved_name, skin_name=reserved_name)

    job = build_export_plan((record,), ExportSettings(), tmp_path)[0]

    assert job.output_path.parts[-2] == f"_{reserved_name}"
    assert job.output_path.name == f"{reserved_name}_角色立绘_4.png"
