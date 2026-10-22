"""Player-facing, allowlisted exports from gacha records and local Lua tables."""

import csv
import json
import re
from collections import Counter
from datetime import datetime, timedelta, timezone
from pathlib import Path

from .gacha import GachaError

CHINA = timezone(timedelta(hours=8))


def entries(path):
    text = path.read_text(encoding="utf-8-sig")
    return {
        int(key): body
        for key, body in re.findall(
            r"^  \[(\d+)\] = \{(.*?)^  \},?", text, re.MULTILINE | re.DOTALL
        )
    }


class Catalog:
    @classmethod
    def from_data(cls, data, fingerprint=""):
        value = cls.__new__(cls)
        value.version = data["version"]
        value.fingerprint = fingerprint or value.version
        value.cards = {int(k): tuple(v) for k, v in data["cards"].items()}
        value.pools = {int(k): v for k, v in data["pools"].items()}
        value.selectors = data["selectors"]
        value.pool_meta = data.get("pool_meta", {})
        return value

    def __init__(self, root):
        self.root = Path(root)
        bundled = self.root / "catalog.json"
        if bundled.is_file():
            data = json.loads(bundled.read_text(encoding="utf-8"))
            self.version = data["version"]
            self.cards = {int(k): tuple(v) for k, v in data["cards"].items()}
            self.pools = {int(k): v for k, v in data["pools"].items()}
            self.selectors = data["selectors"]
            self.pool_meta = data.get("pool_meta", {})
            return
        self.version = self.root.name
        words_text = (self.root / "BaseWord_cn.lua").read_text(encoding="utf-8-sig")
        words = {
            int(key): name
            for key, name in re.findall(
                r'\[(\d+)\]\s*=\s*\{\s*id\s*=\s*\d+,\s*name\s*=\s*"((?:\\.|[^"\\])*)"',
                words_text,
            )
        }

        def name(body, fallback):
            match = re.search(r"\bname\s*=\s*function\(\)\s*return T\((\d+)\)", body)
            return words.get(int(match[1]), fallback) if match else fallback

        self.cards = {}
        for key, body in entries(self.root / "BaseCard.lua").items():
            star = re.search(r"\bstar\s*=\s*(\d+)", body)
            self.cards[key] = (
                name(body, f"未知角色({key})"),
                int(star[1]) if star else None,
            )
        self.pools = {}
        selectors = set()
        for key, body in entries(self.root / "BaseGacha.lua").items():
            self.pools[key] = name(body, f"未知卡池({key})")
            match = re.search(r"\bbottom_main_type\s*=\s*(\d+)", body)
            selectors.add(int(match[1]) if match and int(match[1]) else key)
        self.selectors = sorted(selectors)
        self.pool_meta = {}
        if not self.cards or not self.pools:
            raise GachaError("EMPTY_LUA_CATALOG")


def make_report(records, catalog, query_results):
    from .analysis import analyze_history

    output = []
    stars = Counter()
    for record in records:
        drops = []
        for drop in record["drops"]:
            item_id, count = drop["item_id"], drop["count"]
            label, star = catalog.cards.get(item_id, (f"未知物品({item_id})", None))
            stars[str(star) if star is not None else "unknown"] += count
            drops.append(
                {"item_id": item_id, "name": label, "rarity": star, "count": count}
            )
        stamp = record["timestamp"]
        output.append(
            {
                "pool_id": record["pool_id"],
                "pool_name": catalog.pools.get(
                    record["pool_id"], f"未知卡池({record['pool_id']})"
                ),
                "timestamp": stamp,
                "time_beijing": datetime.fromtimestamp(stamp, CHINA).strftime(
                    "%Y-%m-%d %H:%M:%S"
                ),
                "index": record["index"],
                "record_id": record["record_id"],
                "drops": drops,
            }
        )
    pulls = sum(stars.values())
    pools = Counter()
    for record in output:
        pools[record["pool_name"]] += sum(drop["count"] for drop in record["drops"])
    return {
        "schema_version": 1,
        "captured_at": datetime.now(CHINA).isoformat(),
        "configuration_version": catalog.version,
        "coverage": "仅包含本次服务端返回的历史；保留周期与完整性未确认，不能据此计算当前保底。",
        "summary": {
            "record_count": len(output),
            "pull_count": pulls,
            "rarity_counts": dict(stars),
            "pool_pull_counts": dict(pools),
            "unknown_item_count": stars.get("unknown", 0),
        },
        "queries": query_results,
        "records": output,
        "analysis": analyze_history(output, catalog.pool_meta),
    }


def write_reports(report, output_dir):
    root = Path(output_dir)
    root.mkdir(parents=True, exist_ok=True)
    paths = {
        kind: root / f"gacha-history.{ext}"
        for kind, ext in [("json", "json"), ("csv", "csv"), ("markdown", "md")]
    }
    # Refuse to overwrite a previous export silently.
    if any(path.exists() for path in paths.values()):
        raise GachaError("OUTPUT_ALREADY_EXISTS")
    paths["json"].write_text(
        json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    with paths["csv"].open("w", encoding="utf-8-sig", newline="") as stream:
        writer = csv.writer(stream)
        writer.writerow(
            [
                "时间（北京时间）",
                "卡池",
                "记录序号",
                "记录ID",
                "抽次（该记录内）",
                "物品ID",
                "角色/物品",
                "初始星级",
                "数量",
            ]
        )
        for record in report["records"]:
            for position, drop in enumerate(record["drops"], 1):
                writer.writerow(
                    [
                        record["time_beijing"],
                        record["pool_name"],
                        record["index"],
                        record["record_id"],
                        position,
                        drop["item_id"],
                        drop["name"],
                        drop["rarity"],
                        drop["count"],
                    ]
                )
    summary = report["summary"]

    def cell(value):
        return str(value).replace("|", "\\|").replace("\n", " ")

    lines = [
        "# 星落抽卡记录",
        "",
        f"查询时间：{report['captured_at']}（北京时间）。",
        "",
        f"共 **{summary['record_count']} 条记录，{summary['pull_count']} 抽**。",
        "",
        report["coverage"],
        "",
        "## 星级统计",
        "",
        "| 初始星级 | 数量 | 占比 |",
        "|---|---:|---:|",
    ]
    for star, count in sorted(summary["rarity_counts"].items(), reverse=True):
        ratio = count / summary["pull_count"] * 100 if summary["pull_count"] else 0
        lines.append(
            f"| {star if star != 'unknown' else '未映射'} | {count} | {ratio:.2f}% |"
        )
    lines += ["", "## 卡池统计", "", "| 卡池 | 抽数 |", "|---|---:|"]
    for pool, count in summary["pool_pull_counts"].items():
        lines.append(f"| {cell(pool)} | {count} |")
    lines += [
        "",
        "## 抽卡明细",
        "",
        "按时间正序，每行是一条服务端记录；同一次多抽中的重复角色保留。",
        "",
        "| 时间（北京时间） | 卡池 | 结果 |",
        "|---|---|---|",
    ]
    for record in report["records"]:
        detail = "、".join(
            f"{d['name']}（{d['rarity'] if d['rarity'] is not None else '?'}★）×{d['count']}"
            for d in record["drops"]
        )
        lines.append(
            f"| {record['time_beijing']} | {cell(record['pool_name'])} | {cell(detail)} |"
        )
    lines += [
        "",
        "## 查询范围",
        "",
        "| 查询分组/池 ID | 业务码 | 返回记录数 |",
        "|---|---:|---:|",
    ]
    for result in report["queries"]:
        lines.append(
            f"| {result['selector']} | {result['ret_code']} | {result['record_count']} |"
        )
    lines += [
        "",
        f"名称与初始星级依据本地 Lua 配置 {report['configuration_version']}；未知编号原样保留。",
        "报告不包含登录凭据、手机号、账号或设备标识。星级占比仅描述返回样本，不代表未来概率。",
        "",
    ]
    paths["markdown"].write_text("\n".join(lines), encoding="utf-8")
    return paths
