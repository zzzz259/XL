import hashlib
import json
import os
import stat
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from server_app.gacha_catalog import build_gacha_catalog, publish_catalog


class GachaCatalogTests(unittest.TestCase):
    @unittest.skipUnless(os.name == "posix", "Unix static web server file permissions")
    def test_public_json_readable_under_private_umask_and_republish_repairs_modes(self):
        c = build_gacha_catalog(self.root, "123")
        out = self.root / "public"
        previous = os.umask(0o077)
        try:
            publish_catalog(c, out)
        finally:
            os.umask(previous)
        self.assertEqual(stat.S_IMODE(out.stat().st_mode), 0o755)
        for name in ("current.json", "catalog-123.json"):
            path = out / name
            self.assertEqual(stat.S_IMODE(path.stat().st_mode), 0o644)
            path.chmod(0o600)
        publish_catalog(c, out)
        for name in ("current.json", "catalog-123.json"):
            self.assertEqual(stat.S_IMODE((out / name).stat().st_mode), 0o644)

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        tables = {
            "BaseWord_cn": '[1]={name="测试五星"},[2]={name="测试池"}',
            "BaseCard": "[101]={name=function() return T(1) end,star=5}",
            "BaseGacha": "[201]={name=function() return T(2) end,type=2,bottom_main_type=1,bottom_up=31,main_card_ids={101},bottom_pool={41}}",
            "BaseGachaBottomUp": "[31]={bottom_type=201}",
            "BaseGachaBottomPool": "[41]={type=1,bottom_num=70}",
        }
        for name, body in tables.items():
            (self.root / (name + ".lua")).write_text(
                name + "={" + body + "}", encoding="utf-8"
            )

    def tearDown(self):
        self.temp.cleanup()

    def test_five_static_tables_produce_client_catalog_without_executing_lua(self):
        c = build_gacha_catalog(self.root, "134359843258232661")
        self.assertEqual(c["cards"], {"101": ["测试五星", 5]})
        self.assertEqual(c["pools"], {"201": "测试池"})
        self.assertEqual(c["selectors"], [1])
        self.assertEqual(c["pool_meta"]["201"]["featured_ids"], [101])
        self.assertEqual(c["pool_meta"]["201"]["category"], "limited")
        self.assertEqual(c["pool_meta"]["201"]["hard_pity"], 70)

    def test_publish_hash_and_size_match_immutable_file_and_current_changes_last(self):
        c = build_gacha_catalog(self.root, "123")
        out = self.root / "public"
        m = publish_catalog(c, out)
        raw = (out / m["file"]).read_bytes()
        self.assertEqual(m["sha256"], hashlib.sha256(raw).hexdigest())
        self.assertEqual(m["size"], len(raw))
        self.assertEqual(json.loads((out / "current.json").read_text()), m)
        same = publish_catalog(c, out)
        self.assertEqual(same["sha256"], m["sha256"])
        c["cards"]["101"][0] = "冲突内容"
        with self.assertRaises(ValueError):
            publish_catalog(c, out)
        self.assertEqual(json.loads((out / "current.json").read_text()), same)

    def test_incomplete_tables_cannot_publish_a_partial_catalog(self):
        (self.root / "BaseGachaBottomPool.lua").unlink()
        with self.assertRaises(FileNotFoundError):
            build_gacha_catalog(self.root, "123")

    def test_downgrade_and_invalid_reference_leave_current_unchanged(self):
        c = build_gacha_catalog(self.root, "123")
        out = self.root / "public"
        publish_catalog(c, out)
        previous = (out / "current.json").read_bytes()
        c["version"] = "122"
        with self.assertRaises(ValueError):
            publish_catalog(c, out)
        c["version"] = "124"
        c["pool_meta"]["201"]["featured_ids"] = [999]
        with self.assertRaises(ValueError):
            publish_catalog(c, out)
        self.assertEqual((out / "current.json").read_bytes(), previous)

    def test_pointer_write_failure_preserves_previous_publication(self):
        c = build_gacha_catalog(self.root, "123")
        out = self.root / "public"
        publish_catalog(c, out)
        previous = (out / "current.json").read_bytes()
        c["version"] = "124"
        with (
            patch(
                "server_app.gacha_catalog.os.replace", side_effect=OSError("disk full")
            ),
            self.assertRaises(OSError),
        ):
            publish_catalog(c, out)
        self.assertEqual((out / "current.json").read_bytes(), previous)
        self.assertTrue((out / "catalog-124.json").is_file())

    def test_rejects_invalid_schema_fields(self):
        import copy

        c = build_gacha_catalog(self.root, "123")
        mutations = [
            lambda x: x.update(version="../1"),
            lambda x: x.update(selectors=[1, 1]),
            lambda x: x["cards"]["101"].__setitem__(1, True),
            lambda x: x["cards"]["101"].__setitem__(0, "a\nname"),
            lambda x: x["pool_meta"]["201"].update(hard_pity=1001),
            lambda x: x["pool_meta"]["201"].update(featured_names=["wrong"]),
        ]
        for mutate in mutations:
            with self.subTest(mutation=mutate):
                invalid = copy.deepcopy(c)
                mutate(invalid)
                with self.assertRaises(ValueError):
                    publish_catalog(invalid, self.root / "public")

    def test_cli_exports_only_public_catalog_files(self):
        out = self.root / "public"
        result = subprocess.run(
            [
                sys.executable,
                "-m",
                "server_app.gacha_catalog",
                "--lua-root",
                str(self.root),
                "--version",
                "123",
                "--output",
                str(out),
            ],
            check=False,
            capture_output=True,
            text=True,
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(
            sorted(p.name for p in out.iterdir()), ["catalog-123.json", "current.json"]
        )

    def test_categories_and_five_star_featured_filter(self):
        (self.root / "BaseCard.lua").write_text(
            'BaseCard={[101]={name="五星",star=5},[102]={name="四星",star=4}}',
            encoding="utf-8",
        )
        (self.root / "BaseGacha.lua").write_text(
            "BaseGacha={"
            "[201]={type=0,bottom_main_type=1},[202]={type=1,bottom_main_type=1},"
            "[203]={type=2,bottom_main_type=2,bottom_up=32,main_card_ids={[1]=101,[2]=102}},"
            "[204]={type=2,bottom_main_type=2}}",
            encoding="utf-8",
        )
        (self.root / "BaseGachaBottomUp.lua").write_text(
            "BaseGachaBottomUp={[32]={bottom_type=301}}", encoding="utf-8"
        )
        c = build_gacha_catalog(self.root, "123")
        self.assertEqual(
            [c["pool_meta"][str(k)]["category"] for k in range(201, 205)],
            ["normal", "beginner", "collection", "unknown"],
        )
        self.assertEqual(c["pool_meta"]["203"]["featured_ids"], [101])
        self.assertEqual(c["pool_meta"]["204"]["pity_group"], "unknown:204")
        self.assertIsNone(c["pool_meta"]["204"]["hard_pity"])

    def test_oversized_catalog_cannot_replace_current(self):
        c = build_gacha_catalog(self.root, "123")
        out = self.root / "public"
        publish_catalog(c, out)
        previous = (out / "current.json").read_bytes()
        c["version"] = "124"
        c["oversized"] = "x" * (4 * 1024 * 1024)
        with self.assertRaises(ValueError):
            publish_catalog(c, out)
        self.assertEqual((out / "current.json").read_bytes(), previous)
