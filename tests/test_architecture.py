from pathlib import Path


CORE_DIR = Path(__file__).parents[1] / "xl_updata_tool" / "app" / "core"


def test_core_layer_does_not_import_qt():
    """Keep the core layer usable in CLI/tests without a Qt runtime."""
    offenders = []
    for path in CORE_DIR.glob("*.py"):
        text = path.read_text(encoding="utf-8")
        if "PySide6" in text or "PyQt" in text:
            offenders.append(path.name)

    assert offenders == []
