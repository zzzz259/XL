"""历史图鉴/立绘回填命令（薄入口）。

默认只预检（零下载、零渲染）::

    python scripts/history_backfill.py --config config.toml --db /path/xl_updata.db \
        --work-root /path/backfill-work

真正执行加 ``--execute``；把已校验版本发布到配置的数据根加 ``--publish``
（发布是单独阶段，不与 --execute 同轮进行，也不接受样本过滤）。两个模式都
可用重复 ``--version`` 限定版本；执行模式可用重复 ``--character`` /
``--skin`` 限定角色/皮肤样本（样本范围记入完成记录，过滤版本不会被发布）。
"""

from __future__ import annotations

import argparse
import json
import logging
import sys
from dataclasses import asdict
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from server_app.cdn import CdnClient
from server_app.config import load_config
from server_app.history_backfill import publish_backfill, run_backfill


def main() -> int:
    parser = argparse.ArgumentParser(description="历史图鉴/立绘内容回填")
    parser.add_argument("--config", default="config.toml", help="服务器配置路径")
    parser.add_argument("--db", required=True, help="更新工具 xl_updata.db 路径（只读打开）")
    parser.add_argument("--work-root", required=True, help="独立回填工作根")
    parser.add_argument(
        "--version",
        action="append",
        type=int,
        dest="versions",
        default=None,
        help="限定历史版本（可重复）；缺省为 DB 全部",
    )
    parser.add_argument(
        "--character",
        action="append",
        dest="characters",
        default=None,
        help="执行时只处理指定角色样本（可重复 character_id）",
    )
    parser.add_argument(
        "--skin",
        action="append",
        dest="skins",
        default=None,
        help="执行时只渲染指定皮肤样本（可重复 skin_id，显式过滤，避免整角色渲染）",
    )
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--execute", action="store_true", help="下载并渲染（缺省只预检）")
    mode.add_argument("--publish", action="store_true", help="发布已校验版本到配置的数据根")
    args = parser.parse_args()

    if args.publish and (args.characters or args.skins):
        parser.error("--publish 不接受 --character/--skin（样本过滤只在执行阶段有意义）")

    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    config = load_config(args.config)

    if args.publish:
        report = publish_backfill(
            work_root=args.work_root,
            production_data_dir=config.data_dir,
            versions=args.versions,
        )
        print(json.dumps(asdict(report), ensure_ascii=False))
        return 1 if report.rejected else 0

    client = CdnClient(config.cdn_base)
    report = run_backfill(
        config=config,
        client=client,
        db_path=args.db,
        work_root=args.work_root,
        versions=args.versions,
        execute=args.execute,
        only_character_ids=args.characters,
        only_skin_ids=args.skins,
    )
    print(json.dumps({"versions": [asdict(item) for item in report.versions]}, ensure_ascii=False))
    return 1 if any(item.status == "failed" for item in report.versions) else 0


if __name__ == "__main__":
    raise SystemExit(main())
