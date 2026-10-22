"""Rebuild the non-personal bundled catalog from an explicitly supplied Lua tree."""

import argparse
import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from gacha_core.gacha_report import Catalog, entries


def number(body, key, default=0):
    match = re.search(r"\b" + re.escape(key) + r"\s*=\s*(\d+)", body)
    return int(match[1]) if match else default


def array(body, key):
    match = re.search(r"\b" + re.escape(key) + r"\s*=\s*\{([^}]*)\}", body)
    return [int(v) for v in re.findall(r"\d+", match[1])] if match else []


def build(lua_root):
    catalog = Catalog(lua_root)
    bottomups = entries(lua_root / "BaseGachaBottomUp.lua")
    bottompools = entries(lua_root / "BaseGachaBottomPool.lua")
    metadata = {}
    for pool_id, body in entries(lua_root / "BaseGacha.lua").items():
        kind, group = number(body, "type"), number(body, "bottom_main_type")
        bottom_type = number(
            bottomups.get(number(body, "bottom_up"), ""), "bottom_type"
        )
        category = (
            "normal"
            if kind == 0
            else "beginner"
            if kind == 1
            else "limited"
            if group == 1 and bottom_type == 201
            else "collection"
            if group == 2 and bottom_type == 301
            else "unknown"
        )
        featured = [
            card
            for card in array(body, "main_card_ids")
            if catalog.cards.get(card, ("", None))[1] == 5
        ]
        hard_pity = next(
            (
                number(bottompools.get(k, ""), "bottom_num")
                for k in array(body, "bottom_pool")
                if number(bottompools.get(k, ""), "type") == 1
            ),
            None,
        )
        metadata[pool_id] = {
            "category": category,
            "pity_group": category if category != "unknown" else f"unknown:{pool_id}",
            "featured_ids": featured,
            "featured_names": [catalog.cards[card][0] for card in featured],
            "hard_pity": hard_pity,
        }
    return {
        "version": catalog.version,
        "cards": catalog.cards,
        "pools": {k: v.strip() for k, v in catalog.pools.items()},
        "selectors": catalog.selectors,
        "pool_meta": metadata,
    }


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--lua-root", required=True, type=Path)
    args = parser.parse_args()
    data = build(args.lua_root)
    (ROOT / "data" / "catalog.json").write_text(
        json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    print(f"Updated {len(data['pool_meta'])} pools from version {data['version']}")
