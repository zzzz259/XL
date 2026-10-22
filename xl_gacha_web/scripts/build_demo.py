"""Create explicitly fictional multi-banner examples, never account history."""

import json
import sys
from collections import defaultdict
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from gacha_core.gacha_report import Catalog, make_report


def build():
    catalog = Catalog(ROOT / "data")
    filler = next(k for k, v in catalog.cards.items() if v[1] == 3)
    four = next(k for k, v in catalog.cards.items() if v[1] == 4)
    five = [k for k, v in catalog.cards.items() if v[1] == 5]
    index = defaultdict(int)
    records = []
    stamp = int(datetime(2026, 4, 1, tzinfo=timezone(timedelta(hours=8))).timestamp())

    def batch(pool_id, sequence):
        nonlocal stamp
        meta = catalog.pool_meta[str(pool_id)]
        selector = {
            "limited": 1,
            "collection": 2,
            "normal": pool_id,
            "beginner": pool_id,
        }[meta["category"]]
        for start in range(0, len(sequence), 10):
            index[selector] += 1
            records.append(
                {
                    "pool_id": pool_id,
                    "timestamp": stamp,
                    "index": index[selector],
                    "record_id": selector,
                    "drops": [
                        {"item_id": card, "tuple_type": 2, "count": 1}
                        for card in sequence[start : start + 10]
                    ],
                }
            )
            stamp += 60
        stamp += 15 * 86400

    def hit(pool_id):
        return catalog.pool_meta[str(pool_id)]["featured_ids"][0]

    def off(pool_id):
        return next(
            k for k in five if k not in catalog.pool_meta[str(pool_id)]["featured_ids"]
        )

    batch(24000001, [filler] * 9 + [four] + [filler] * 20)
    batch(
        24000002, [filler] * 32 + [five[0]] + [filler] * 64 + [five[1]] + [filler] * 17
    )
    batch(24000003, [filler] * 52 + [off(24000003)] + [filler] * 7)
    batch(
        24000004,
        [filler] * 13
        + [hit(24000004)]
        + [filler] * 61
        + [off(24000004)]
        + [filler] * 11
        + [hit(24000004)]
        + [filler] * 8,
    )
    batch(24000013, [filler] * 66 + [off(24000013)] + [filler] * 5)
    batch(24000021, [filler] * 14 + [hit(24000021)] + [filler] * 20)
    batch(24000005, [filler] * 40 + [hit(24000005)] + [filler] * 15)
    report = make_report(
        records,
        catalog,
        [{"selector": s, "ret_code": 0, "record_count": n} for s, n in index.items()],
    )
    report.update(
        demo=True, partial=False, coverage="完全虚构的演示数据，不对应任何游戏账号。"
    )
    return report


if __name__ == "__main__":
    report = build()
    (ROOT / "static" / "demo.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    print(
        "Built fictional career demo with", len(report["analysis"]["pools"]), "banners"
    )
