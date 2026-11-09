"""Read atlas page names without loading publication, query, or image support."""

from pathlib import Path


_IMAGE_SUFFIXES = frozenset({".bmp", ".gif", ".jpeg", ".jpg", ".png", ".tga", ".webp"})


def _atlas_texture_names(atlas_path: Path) -> tuple[str, ...]:
    """Read page names from both .atlas and Unity .atlas.txt files."""
    if not atlas_path.is_file():
        return ()
    try:
        lines = atlas_path.read_text(encoding="utf-8", errors="replace").splitlines()
    except OSError:
        return ()
    names: list[str] = []
    seen: set[str] = set()
    for raw_line in lines:
        line = raw_line.strip()
        if not line or ":" in line:
            continue
        candidate = Path(line)
        if candidate.suffix.casefold() not in _IMAGE_SUFFIXES:
            continue
        normalized = candidate.as_posix()
        if normalized not in seen:
            names.append(normalized)
            seen.add(normalized)
    return tuple(names)
