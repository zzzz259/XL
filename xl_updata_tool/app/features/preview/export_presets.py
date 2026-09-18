"""Built-in Spine export presets used by the preview workbench."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class SpineExportPreset:
    resource_family: str
    default_animation: str = "idle"
    default_skins: tuple[str, ...] = ("default",)
    allow_video: bool = False
    static_scale: int = 1
    video_scale: int = 1
    max_resolution: int = 16000
    margin: int = 10
    video_fps: int = 30
    video_background: str = "#7f7f7f"


def preset_for_family(resource_family: str | None) -> SpineExportPreset | None:
    family = str(resource_family or "").strip().casefold()
    if family == "cardspine":
        return SpineExportPreset(
            resource_family=family,
            default_animation="idle",
            default_skins=("default",),
            allow_video=True,
            static_scale=4,
            video_scale=1,
        )
    if family == "battlespine":
        return SpineExportPreset(
            resource_family=family,
            default_animation="idle",
            default_skins=("motion_stander",),
            allow_video=False,
            static_scale=1,
            video_scale=1,
        )
    if family == "eventcovers":
        return SpineExportPreset(
            resource_family=family,
            default_animation="idle",
            default_skins=("default",),
            allow_video=False,
            static_scale=1,
            video_scale=1,
        )
    return None


def default_export_settings(resource_family, output_format, animation_duration=None):
    """Build immutable settings for one builtin output artifact."""
    from .export_plan import ExportSettings

    family = str(resource_family or "").strip().casefold()
    normalized_format = str(output_format or "").strip().casefold()
    preset = preset_for_family(family)
    if normalized_format not in {"png", "mp4"}:
        raise ValueError("Builtin Spine export supports PNG or MP4")
    if normalized_format == "mp4" and (preset is None or not preset.allow_video):
        raise ValueError(f"{family or 'This resource'} does not support builtin video export")

    is_static = normalized_format == "png"
    # ``None`` deliberately leaves ``--duration`` out of the CLI command.
    # SpineViewerCLI then uses its documented ``-1`` default, which exports
    # the complete selected animation instead of clipping it to a guessed
    # duration.
    duration = None
    return ExportSettings(
        animation=preset.default_animation if preset else "idle",
        static=is_static,
        scale=(preset.static_scale if is_static else preset.video_scale) if preset else 1,
        max_resolution=preset.max_resolution if preset else 16000,
        margin=preset.margin if preset else 10,
        transparent=is_static,
        pma=True,
        format="Png" if is_static else "Mp4",
        fps=1 if is_static else (preset.video_fps if preset else 30),
        duration=duration,
        skins=preset.default_skins if preset else ("default",),
        background_color=("#00000000" if is_static else preset.video_background)
        if preset
        else ("#00000000" if is_static else "#7f7f7f"),
        auto_border=True,
    )
