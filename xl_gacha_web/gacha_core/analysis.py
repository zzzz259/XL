"""Derived history statistics; never claims an unobserved server pity state."""

CATEGORIES = {
    "normal": "普通池",
    "limited": "限定池",
    "collection": "典藏池",
    "beginner": "启程池",
    "unknown": "未分类",
}
ANALYSIS_VERSION = 2


def analyze_history(records: list[dict], metadata: dict) -> dict:
    categories = {
        key: {
            "name": name,
            "pull_count": 0,
            "five_count": 0,
            "up_count": 0,
            "off_count": 0,
            "guaranteed_count": 0,
            "average_five_interval": None,
            "since_five": 0,
            "since_five_exact": False,
            "next_guaranteed": None,
            "hard_pity": None,
        }
        for key, name in CATEGORIES.items()
    }
    groups, pools = {}, {}
    for record in sorted(
        records,
        key=lambda r: (r["timestamp"], r["index"], r["record_id"], r["pool_id"]),
    ):
        pool_id = record["pool_id"]
        meta = metadata.get(pool_id, metadata.get(str(pool_id), {}))
        category = meta.get("category", "unknown")
        if category not in categories:
            category = "unknown"
        group = meta.get("pity_group", f"unknown:{pool_id}")
        state = groups.setdefault(
            group,
            {
                "since": 0,
                "exact": False,
                "guarantee": None,
                "last_index": None,
                "pool": None,
                "carried": 0,
            },
        )
        index = record.get("index", 0)
        # Missing records can hide both a pity reset and a guaranteed UP. Reset
        # inference rather than connecting across an unobserved gap.
        # The server numbers stored records in a 1..80 ring, per pity group.
        previous = state["last_index"]
        continuous = (
            previous is None or index == previous + 1 or (previous == 80 and index == 1)
        )
        if previous is not None and (index <= 0 or not continuous):
            state.update(since=0, exact=False, guarantee=None, carried=0)
        state["last_index"] = index
        if state["pool"] != pool_id:
            state.update(pool=pool_id, carried=state["since"])
        stats = categories[category]
        stats["hard_pity"] = meta.get("hard_pity")
        pool = pools.setdefault(
            pool_id,
            {
                "pool_id": pool_id,
                "pool_name": record["pool_name"],
                "category": category,
                "category_name": CATEGORIES[category],
                "hard_pity": meta.get("hard_pity"),
                "featured_names": meta.get("featured_names", []),
                "pull_count": 0,
                "five_count": 0,
                "record_count": 0,
                "first_date": record["time_beijing"][:10],
                "last_date": record["time_beijing"][:10],
                "latest_timestamp": record["timestamp"],
                "events": [],
                "characters": {},
                "tail_since_five": 0,
                "tail_exact": False,
                "next_guaranteed": None,
            },
        )
        pool["record_count"] += 1
        pool["last_date"] = record["time_beijing"][:10]
        pool["latest_timestamp"] = record["timestamp"]
        for drop in record["drops"]:
            count = max(0, int(drop["count"]))
            if count > 10000:
                raise ValueError("invalid drop quantity")
            stats["pull_count"] += count
            char = pool["characters"].setdefault(
                drop["item_id"],
                {
                    "item_id": drop["item_id"],
                    "name": drop["name"],
                    "rarity": drop["rarity"],
                    "count": 0,
                },
            )
            char["count"] += count
            if drop["rarity"] != 5:
                state["since"] += count
                pool["pull_count"] += count
                if drop["rarity"] is None:
                    # An unmapped item may itself be a five-star character.
                    state.update(since=0, exact=False, guarantee=None, carried=0)
                continue
            for _ in range(count):
                state["since"] += 1
                pool["pull_count"] += 1
                featured = meta.get("featured_ids", [])
                outcome = (
                    ("up" if drop["item_id"] in featured else "off")
                    if category in ("limited", "collection") and featured
                    else "normal"
                    if category in ("normal", "beginner")
                    else "unknown"
                )
                guaranteed = (
                    state["guarantee"]
                    if outcome == "up"
                    else False
                    if outcome == "off"
                    else None
                )
                conflict = bool(state["guarantee"] is True and outcome == "off")
                pool["events"].append(
                    {
                        "name": drop["name"],
                        "item_id": drop["item_id"],
                        "pulls_since_five": state["since"],
                        "interval_exact": state["exact"],
                        "outcome": outcome,
                        "guaranteed": guaranteed,
                        "guarantee_conflict": conflict,
                        "carried_pulls": state["carried"],
                        "in_pool_pull": pool["pull_count"],
                        "timestamp": record["timestamp"],
                        "time_beijing": record["time_beijing"],
                    }
                )
                pool["five_count"] += 1
                stats["five_count"] += 1
                stats["up_count"] += outcome == "up"
                stats["off_count"] += outcome == "off"
                stats["guaranteed_count"] += guaranteed is True
                state.update(
                    since=0,
                    exact=True,
                    guarantee=True
                    if outcome == "off"
                    else False
                    if outcome == "up"
                    else None,
                )
                state["carried"] = 0
        pool.update(
            tail_since_five=state["since"],
            tail_exact=state["exact"],
            next_guaranteed=state["guarantee"],
        )
        stats.update(
            since_five=state["since"],
            since_five_exact=state["exact"],
            next_guaranteed=state["guarantee"],
        )
    for stats in categories.values():
        count = stats["five_count"]
        # Integer half-up rounding keeps Windows and Android identical at x.xx5.
        stats["average_five_interval"] = (
            ((stats["pull_count"] * 200 + count) // (2 * count)) / 100
            if count
            else None
        )
    for pool in pools.values():
        pool["characters"] = sorted(
            pool["characters"].values(),
            key=lambda c: (-(c["rarity"] or 0), -c["count"], c["item_id"]),
        )
    return {
        "version": ANALYSIS_VERSION,
        "categories": categories,
        "pools": sorted(
            pools.values(), key=lambda p: (-p["latest_timestamp"], -p["pool_id"])
        ),
        "method": "按记录时间、序号和服务端掉落顺序展开；首次可见五星与断档后的间隔为下界。大保底仅根据连续可见记录推断。",
        "order_note": "十连内按服务端返回顺序计算；未独立核验真实逐抽顺序。",
        "rules_source": "本地游戏配置：BaseGacha / BaseGachaBottomUp / BaseGachaBottomPool；BaseWord 80614053/55/63/65。",
    }
