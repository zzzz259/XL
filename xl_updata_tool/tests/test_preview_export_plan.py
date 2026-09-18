from dataclasses import FrozenInstanceError

import pytest

from app.features.preview.export_plan import (
    ExportSettings,
    SkinExportJob,
    build_export_plan,
    build_spine_export_command,
)
from app.features.preview.resource_model import SpineSkinRecord, skin_key


def ready_record(
    *,
    character_id="10080",
    skin_name="holiday",
    attachment_fingerprint="abc",
    status="ready",
):
    return SpineSkinRecord(
        character_id=character_id,
        source_skel="assets/cardspine_10080.skel",
        atlas_path="assets/cardspine_10080.atlas",
        skin_name=skin_name,
        attachment_fingerprint=attachment_fingerprint,
        display_name=skin_name,
        status=status,
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
    assert plan[0].output_path == tmp_path / "10080" / skin_key(record) / "holiday.png"


def test_export_command_uses_internal_skin_and_ui_settings(tmp_path):
    record = ready_record(character_id="10080", skin_name="skin_internal", attachment_fingerprint="abc")
    job = build_export_plan((record,), ExportSettings(animation="walk", static=False, scale=2, margin=8), tmp_path)[0]

    command = build_spine_export_command(job, "SpineViewerCLI.exe")

    assert command[command.index("--skins") + 1] == "skin_internal"
    assert command[command.index("-a") + 1] == "walk"
    assert command[command.index("--scale") + 1] == "2"
    assert command[command.index("--margin") + 1] == "8"
    assert command[command.index("--max-resolution") + 1] == "8192"
    assert command[command.index("--fps") + 1] == "1"
    assert command[command.index("--color") + 1] == "#00000000"
    assert "--pma" in command


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

    with pytest.raises(ValueError, match="PNG"):
        build_export_plan((record,), ExportSettings(format="Jpeg"), tmp_path)


def test_export_plan_sanitizes_skin_filename_without_changing_internal_identity(tmp_path):
    record = ready_record(skin_name="holiday:night")

    job = build_export_plan((record,), ExportSettings(), tmp_path)[0]

    assert job.record.skin_name == "holiday:night"
    assert job.output_path.name == "holiday_night.png"


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

    assert job.output_path.parts[-3] == f"_{reserved_name}"
    assert job.output_path.name == f"_{reserved_name}.png"
