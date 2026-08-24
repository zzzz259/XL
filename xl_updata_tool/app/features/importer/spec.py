"""Importer 使用的资源分类规格。"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class ExportRule:
    asset_type: str | None = None
    name_regex: str | None = None
    container_regex: str | None = None

    def cli_args(self) -> tuple[str, ...]:
        args: list[str] = []
        if self.asset_type:
            args.extend(("--types", self.asset_type))
        if self.name_regex:
            args.extend(("--names", self.name_regex))
        if self.container_regex:
            args.extend(("--containers", self.container_regex))
        return tuple(args)


@dataclass(frozen=True)
class ExportSpec:
    key: str
    material_relative: str
    rules: tuple[ExportRule, ...]

    def commands(self) -> tuple[tuple[str, tuple[str, ...]], ...]:
        return tuple((f"导出 {self.key}", rule.cli_args()) for rule in self.rules)


EXPORT_SPECS = {
    "lua": ExportSpec(
        "lua", "assets/lua", (ExportRule("TextAsset", container_regex=r"assets/lua"),)
    ),
    "character": ExportSpec(
        "character",
        "assets/art/models",
        (
            ExportRule("TextAsset", container_regex=r"assets/art/models/cardspine"),
            ExportRule("Texture2D", r"^(?!.*_[en]$)", r"assets/art/models/cardspine"),
            ExportRule("TextAsset", r"battlespine_1\d{4}", r"assets/art/models/battlespine"),
            ExportRule("Texture2D", r"battlespine_1\d{4}(?!.*_[en]$)", r"assets/art/models/battlespine"),
        ),
    ),
    "fgui": ExportSpec(
        "fgui",
        "assets/fairygui",
        (
            ExportRule("TextAsset", container_regex=r"assets/fairygui"),
            ExportRule("Texture2D", container_regex=r"assets/fairygui"),
        ),
    ),
    "audio": ExportSpec(
        "audio",
        "assets/fmodassets",
        (
            ExportRule("TextAsset", container_regex=r"assets/fmodassets/voice_cn/btl"),
            ExportRule("TextAsset", container_regex=r"assets/fmodassets/voice_cn/system"),
            ExportRule("TextAsset", container_regex=r"assets/fmodassets/voice_jp/btl"),
            ExportRule("TextAsset", container_regex=r"assets/fmodassets/voice_jp/system"),
            ExportRule("TextAsset", container_regex=r"assets/fmodassets/bgm"),
        ),
    ),
}

CATEGORY_DIRS = {key: spec.material_relative for key, spec in EXPORT_SPECS.items()}


def normalise_categories(categories) -> frozenset[str]:
    """校验并规范化用户勾选的导出分类。"""
    selected = frozenset(
        str(category).strip().lower()
        for category in (categories or ())
        if str(category).strip()
    )
    unknown = selected - EXPORT_SPECS.keys()
    if unknown:
        raise ValueError(f"未知导出分类: {', '.join(sorted(unknown))}")
    return selected


def build_category_commands(categories) -> tuple[tuple[str, list[str]], ...]:
    """将分类规格展开为旧 AssetStudio CLI 所需的命令参数。"""
    selected = normalise_categories(categories)
    commands = []
    for category in sorted(selected):
        commands.extend(
            (label, list(args)) for label, args in EXPORT_SPECS[category].commands()
        )
    return tuple(commands)
