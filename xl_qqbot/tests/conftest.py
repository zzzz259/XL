"""Test bootstrap: make the sibling xl_gacha_web importable without PYTHONPATH.

The qq-bot CI exports both directories via PYTHONPATH; local checkouts running
``cd xl_qqbot && python -m pytest -q`` rely on this conftest instead. The
production wiring (automation, gateway, deploy) is unchanged.
"""

import sys
from pathlib import Path

_GACHA_WEB = Path(__file__).resolve().parents[2] / "xl_gacha_web"
if _GACHA_WEB.is_dir():
    path = str(_GACHA_WEB)
    if path not in sys.path:
        sys.path.insert(0, path)
