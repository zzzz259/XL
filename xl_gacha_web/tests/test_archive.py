import copy
import csv
import io
import json
import tempfile
import unittest
from pathlib import Path

import archive
from gacha_core.gacha_report import Catalog


def sample(index=1, item=101, count=2):
    return {
        "records": [
            {
                "pool_id": 1,
                "timestamp": 1700000000 + index,
                "index": index,
                "record_id": index,
                "drops": [
                    {"item_id": item, "count": count},
                    {"item_id": item, "count": 1},
                ],
            }
        ],
        "jwt": "NEVER_STORE_THIS_SECRET",
    }


class ArchiveTests(unittest.TestCase):
    def test_query_snapshot_is_preserved_then_later_open_can_upgrade_it(self):
        old = copy.deepcopy(self.catalog)
        old.fingerprint = "query-snapshot"
        newer = copy.deepcopy(old)
        newer.fingerprint = "latest-snapshot"
        newer.cards[999001] = ("新名字", 5)
        self.store.use_catalog(newer)
        r = self.store.save_query(sample(item=999001), "100", "sms", catalog=old)
        self.assertEqual(r["configuration_revision"], "query-snapshot")
        self.assertEqual(r["summary"]["unknown_item_count"], 3)
        upgraded = self.store.open(r["account"]["id"])
        self.assertEqual(upgraded["configuration_revision"], "latest-snapshot")
        self.assertEqual(upgraded["summary"]["unknown_item_count"], 0)

    def test_new_catalog_remaps_stored_ids_without_losing_draws_or_account(self):
        a = self.store.save_query(sample(item=999001), "100", "sms")
        self.assertEqual(a["summary"]["unknown_item_count"], 3)
        changed = copy.deepcopy(self.catalog)
        changed.version = "999999999999999999"
        changed.fingerprint = "new-revision"
        changed.cards[999001] = ("新五星", 5)
        changed.pools[1] = "新池名"
        changed.pool_meta["1"] = {
            "category": "normal",
            "pity_group": "normal",
            "featured_ids": [],
            "featured_names": [],
            "hard_pity": 70,
        }
        self.store.use_catalog(changed)
        r = self.store.open(a["account"]["id"])
        self.assertEqual(r["account"], a["account"])
        self.assertEqual(r["captured_at"], a["captured_at"])
        self.assertEqual(r["summary"]["pull_count"], 3)
        self.assertEqual(r["summary"]["unknown_item_count"], 0)
        self.assertEqual(r["records"][0]["drops"][0]["name"], "新五星")
        self.assertEqual(r["records"][0]["pool_name"], "新池名")
        self.assertEqual(r["analysis"]["categories"]["normal"]["five_count"], 3)
        self.assertEqual(r["configuration_revision"], "new-revision")
        self.assertEqual(
            self.store.save_query(sample(item=999001), "100", "sms")["summary"][
                "pull_count"
            ],
            3,
        )

    def test_open_and_backup_recompute_old_analysis_without_requery_or_losing_identity(
        self,
    ):
        a = self.store.save_query(sample(), "100", "sms")
        key = a["account"]["id"]
        a["analysis"] = {
            "categories": {"normal": {"average_five_interval": -1}},
            "pools": [],
        }
        with self.store.connect() as db:
            db.execute("UPDATE accounts SET report=? WHERE id=?", (json.dumps(a), key))
        reopened = self.store.open(key)
        self.assertEqual(reopened["analysis"].get("version"), 2)
        self.assertEqual(reopened["records"], a["records"])
        self.assertEqual(reopened["account"], a["account"])
        self.assertEqual(reopened["captured_at"], a["captured_at"])
        a["analysis"] = {}
        with self.store.connect() as db:
            db.execute("UPDATE accounts SET report=? WHERE id=?", (json.dumps(a), key))
        self.assertEqual(
            self.store.backup()["accounts"][0]["analysis"].get("version"), 2
        )

    def test_ambiguous_legacy_banner_keeps_verified_shared_pity_group_without_claiming_up(
        self,
    ):
        content = "时间（北京时间）,卡池,记录序号,记录ID,抽次（该记录内）,物品ID,角色/物品,初始星级,数量\n2026-01-01 12:00:00,悲悯仁心,80,1,1,999001,测试五星,5,1\n2026-01-01 12:01:00,救赎之光,1,1,1,999002,测试五星2,5,1\n"
        report = self.store.import_data(content, "csv")[0]
        hit = report["analysis"]["pools"][0]["events"][0]
        self.assertTrue(hit["interval_exact"])
        self.assertEqual(hit["pulls_since_five"], 1)
        self.assertEqual(hit["outcome"], "unknown")

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.path = Path(self.temp.name)
        self.catalog = Catalog(Path(__file__).resolve().parents[1] / "data")
        self.store = archive.Archive(self.path, self.catalog)

    def tearDown(self):
        self.temp.cleanup()

    def test_accounts_remain_separate_and_survive_restart(self):
        a = self.store.save_query(sample(), "100", "sms")
        b = self.store.save_query(sample(2), "200", "taptap")
        self.assertNotEqual(a["account"]["id"], b["account"]["id"])
        reopened = archive.Archive(self.path, self.catalog)
        self.assertEqual(len(reopened.list_accounts()), 2)
        self.assertEqual(reopened.open(a["account"]["id"])["summary"]["pull_count"], 3)
        self.assertNotIn(
            b"NEVER_STORE_THIS_SECRET", (self.path / "history.sqlite3").read_bytes()
        )

    def test_update_deduplicates_records_but_preserves_multi_pull_duplicates(self):
        a = self.store.save_query(sample(), "100", "sms")
        combined = sample(2)
        combined["records"] += sample()["records"]
        result = self.store.save_query(combined, "100", "sms")
        self.assertEqual(result["account"]["id"], a["account"]["id"])
        self.assertEqual(result["summary"]["record_count"], 2)
        self.assertEqual(result["summary"]["pull_count"], 6)
        self.assertEqual(len(result["records"][0]["drops"]), 2)

    def test_wrong_target_identity_and_record_conflicts_leave_existing_archive_intact(
        self,
    ):
        a = self.store.save_query(sample(), "100", "sms")
        b = self.store.save_query(sample(2), "200", "sms")
        with self.assertRaises(archive.ArchiveError):
            self.store.import_data(json.dumps(a), "json", b["account"]["id"])
        with self.assertRaises(archive.ArchiveError):
            self.store.save_query(sample(item=999), "100", "sms")
        self.assertEqual(
            self.store.open(a["account"]["id"])["summary"]["pull_count"], 3
        )

    def test_backup_restores_all_accounts_and_renames_without_secrets(self):
        a = self.store.save_query(sample(), "100", "sms")
        self.store.rename(a["account"]["id"], "我的主号")
        self.store.save_query(sample(2), "200", "sms")
        backup = self.store.backup()
        self.assertNotIn("NEVER_STORE_THIS_SECRET", json.dumps(backup))
        other = archive.Archive(self.path / "other", self.catalog)
        other.import_data(json.dumps(backup), "json")
        other.import_data(json.dumps(backup), "json")
        self.assertEqual(len(other.list_accounts()), 2)
        self.assertEqual(other.open(a["account"]["id"])["account"]["label"], "我的主号")
        self.store.delete(a["account"]["id"])
        self.assertEqual(len(self.store.list_accounts()), 1)

    def test_import_is_atomic_if_one_backup_account_is_invalid(self):
        self.store.save_query(sample(), "100", "sms")
        backup = self.store.backup()
        invalid = copy.deepcopy(backup["accounts"][0])
        invalid["account"]["id"] = "local-invalid"
        invalid["records"][0]["drops"][0]["count"] = -1
        backup["accounts"].append(invalid)
        other = archive.Archive(self.path / "other", self.catalog)
        with self.assertRaises(archive.ArchiveError):
            other.import_data(json.dumps(backup), "json")
        self.assertEqual(other.list_accounts(), [])

    def test_legacy_csv_requires_explicit_target_to_merge_and_preserves_positions(self):
        csv = "时间（北京时间）,卡池,记录序号,记录ID,抽次（该记录内）,物品ID,角色/物品,初始星级,数量\n2024-01-01 12:00:00,未收录池,1,10,1,123,测试角色,5,1\n2024-01-01 12:00:00,未收录池,1,10,2,123,测试角色,5,1\n"
        a = self.store.import_data(csv, "csv", label="旧号")[0]
        b = self.store.import_data(csv, "csv")[0]
        self.assertNotEqual(a["account"]["id"], b["account"]["id"])
        self.assertEqual(a["summary"]["pull_count"], 2)
        self.assertEqual(a["records"][0]["drops"][0]["name"], "测试角色")
        self.assertEqual(a["analysis"]["categories"]["unknown"]["five_count"], 2)
        merged = self.store.import_data(csv, "csv", a["account"]["id"])[0]
        self.assertEqual(merged["summary"]["pull_count"], 2)

    def test_invalid_and_empty_inputs_do_not_create_accounts(self):
        for data, fmt in [
            ("not json", "json"),
            ("{}", "json"),
            ("a,b\n1,2", "csv"),
            (json.dumps({"records": [{"jwt": "secret"}]}), "json"),
        ]:
            with self.subTest(data=data), self.assertRaises(archive.ArchiveError):
                self.store.import_data(data, fmt)
        self.assertEqual(self.store.list_accounts(), [])

    def test_csv_keeps_unknown_pool_category_on_round_trip(self):
        source = sample()
        source["records"][0]["pool_id"] = -123
        source["pool_metadata"] = {"-123": {"category": "collection"}}
        report = self.store.import_data(json.dumps(source), "json")[0]
        output = io.StringIO()
        writer = csv.writer(output)
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
                "卡池ID",
                "存档ID",
                "账号名称",
                "账号UID",
                "登录渠道",
                "卡池分类",
            ]
        )
        r = report["records"][0]
        a = report["account"]
        for i, d in enumerate(r["drops"], 1):
            writer.writerow(
                [
                    r["time_beijing"],
                    r["pool_name"],
                    r["index"],
                    r["record_id"],
                    i,
                    d["item_id"],
                    d["name"],
                    d["rarity"] or "",
                    d["count"],
                    r["pool_id"],
                    a["id"],
                    a["label"],
                    "",
                    "",
                    "collection",
                ]
            )
        restored = archive.Archive(self.path / "csv-other", self.catalog).import_data(
            output.getvalue(), "csv"
        )[0]
        self.assertEqual(restored["summary"]["pull_count"], 3)
        self.assertEqual(
            restored["analysis"]["categories"]["collection"]["pull_count"], 3
        )


if __name__ == "__main__":
    unittest.main()
