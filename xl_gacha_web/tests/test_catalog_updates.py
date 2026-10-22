import hashlib
import json
import tempfile
import threading
import unittest
from pathlib import Path

from catalog_updates import (
    CatalogStore,
    CatalogUpdateError,
    validate_catalog,
    validate_manifest,
)


def fixture(version="134359843258232661"):
    return {
        "schema_version": 1,
        "version": version,
        "cards": {"101": ["新角色", 5]},
        "pools": {"201": "新卡池"},
        "selectors": [1],
        "pool_meta": {
            "201": {
                "category": "limited",
                "pity_group": "limited",
                "featured_ids": [101],
                "featured_names": ["新角色"],
                "hard_pity": 70,
            }
        },
    }


def downloads(c):
    raw = json.dumps(c, ensure_ascii=False).encode()
    m = {
        "format": "starfall-gacha-catalog-manifest",
        "schema_version": 1,
        "version": c["version"],
        "file": f"catalog-{c['version']}.json",
        "sha256": hashlib.sha256(raw).hexdigest(),
        "size": len(raw),
    }
    return raw, m


class CatalogUpdateTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.baseline = fixture("100")
        self.source = "https://config.example.test/gacha/current.json"

    def tearDown(self):
        self.temp.cleanup()

    def store(self, loader):
        return CatalogStore(self.root, self.baseline, source=self.source, fetch=loader)

    def test_update_fetches_only_manifest_and_catalog_then_survives_offline_restart(
        self,
    ):
        raw, m = downloads(fixture())
        calls = []

        def load(url, limit):
            calls.append(url)
            return json.dumps(m).encode() if url.endswith("current.json") else raw

        s = self.store(load)
        self.assertTrue(s.check(force=True)["updated"])
        self.assertEqual(s.catalog().cards[101], ("新角色", 5))
        self.assertEqual(
            calls, [self.source, "https://config.example.test/gacha/" + m["file"]]
        )
        old = self.store(lambda *args: (_ for _ in ()).throw(OSError("offline")))
        self.assertEqual(old.catalog().version, fixture()["version"])
        self.assertEqual(old.check(force=True)["phase"], "error")
        self.assertEqual(old.catalog().cards[101][0], "新角色")

    def test_bad_hash_and_version_downgrade_never_replace_cached_catalog(self):
        raw, m = downloads(fixture("200"))
        s = self.store(
            lambda url, limit: (
                json.dumps(m).encode() if url.endswith("current.json") else raw
            )
        )
        s.check(force=True)
        m["sha256"] = "0" * 64
        self.assertEqual(s.check(force=True)["phase"], "error")
        self.assertEqual(s.catalog().version, "200")
        raw, m = downloads(fixture("150"))
        self.assertEqual(s.check(force=True)["phase"], "error")
        self.assertEqual(s.catalog().version, "200")

    def test_untrusted_paths_unknown_schema_and_missing_up_references_are_rejected(
        self,
    ):
        _raw, m = downloads(fixture())
        for file in (
            "../private.json",
            "https://other.test/a.json",
            "catalog-123.json",
        ):
            bad = {**m, "file": file}
            with self.assertRaises(CatalogUpdateError):
                validate_manifest(bad)
        for modify in (
            lambda c: c.update(schema_version=99),
            lambda c: c["pool_meta"]["201"].update(featured_ids=[999]),
            lambda c: c.update(selectors=[]),
        ):
            c = fixture()
            modify(c)
            with self.assertRaises(CatalogUpdateError):
                validate_catalog(c)

    def test_unconfigured_source_keeps_bundled_catalog_available(self):
        s = CatalogStore(self.root, self.baseline, source="")
        self.assertEqual(s.check()["phase"], "unconfigured")
        self.assertEqual(s.catalog().version, "100")

    def test_background_start_exposes_checking_until_download_finishes(self):
        raw, m = downloads(fixture())
        gate = threading.Event()
        entered = threading.Event()

        def loader(url, limit):
            entered.set()
            gate.wait(3)
            return json.dumps(m).encode() if url.endswith("current.json") else raw

        s = self.store(loader)
        try:
            self.assertEqual(s.start(force=True)["phase"], "checking")
            self.assertTrue(entered.wait(2))
        finally:
            gate.set()
            s.worker.join(3)
        self.assertEqual(s.status()["phase"], "updated")

    def test_corrupt_active_pointer_keeps_verified_previous_across_next_update(self):
        raw, m = downloads(fixture("200"))

        def loader(url, limit):
            return json.dumps(m).encode() if url.endswith("current.json") else raw

        s = self.store(loader)
        s.check(True)
        raw, m = downloads(fixture("300"))
        s.check(True)
        (self.root / "active.json").write_text("broken")
        recovered = self.store(loader)
        self.assertEqual(recovered.catalog().version, "200")
        raw, m = downloads(fixture("400"))
        recovered.check(True)
        (self.root / "active.json").write_text("broken")
        self.assertEqual(self.store(loader).catalog().version, "200")
