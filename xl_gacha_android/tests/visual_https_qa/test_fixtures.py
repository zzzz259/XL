import hashlib
import json
import struct
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
CATALOG = {
    "schema_version": 1, "version": "134379197371350448",
    "cards": {str(item): [f"QA {item}", 5] for item in (601, 602, 603, 604)},
    "pools": {"1": "QA"},
    "pool_meta": {"1": {"category": "normal", "pity_group": "normal", "hard_pity": None,
                        "featured_ids": [], "featured_names": []}},
    "selectors": [1],
}
DEMO = {"records": [{"drops": [{"item_id": item, "rarity": 5} for item in (601, 602, 603, 604)]}]}


def descriptor(path):
    raw = path.read_bytes()
    return {"size": len(raw), "sha256": hashlib.sha256(raw).hexdigest()}


class FixturesTest(unittest.TestCase):
    def test_complete_cumulative_snapshots(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            catalog, demo = root / "catalog.json", root / "demo.json"
            catalog.write_text(json.dumps(CATALOG), encoding="utf-8")
            demo.write_text(json.dumps(DEMO), encoding="utf-8")
            command = [sys.executable, "-B", str(HERE / "generate_fixtures.py"),
                       "--catalog", str(catalog), "--demo", str(demo),
                       "--output", str(root / "fixtures")]
            first = subprocess.run(command, capture_output=True, text=True, check=False)
            self.assertEqual(first.returncode, 0, first.stderr)
            fixture = root / "fixtures"
            index = json.loads((fixture / "qa-index.json").read_bytes())
            previous_version = int(CATALOG["version"])
            previous_files = {}
            for stage, count in (("V1", 2), ("V2", 3), ("V3", 4)):
                folder = fixture / stage
                current = json.loads((folder / "current.json").read_bytes())
                self.assertEqual(current["format"], "starfall-gacha-catalog-manifest")
                version = current["version"]
                self.assertGreater(int(version), previous_version)
                previous_version = int(version)
                self.assertEqual(current["file"], f"catalog-{version}.json")
                catalog_path = folder / current["file"]
                self.assertEqual(descriptor(catalog_path), {k: current[k] for k in ("size", "sha256")})
                payload = json.loads(catalog_path.read_bytes())
                self.assertEqual(payload["version"], version)
                self.assertEqual(payload["cards"], CATALOG["cards"])
                visual = payload["visual_assets"]
                self.assertEqual(visual["file"], f"visual-assets-{version}.json")
                mapping_path = folder / visual["file"]
                self.assertEqual(descriptor(mapping_path), {k: visual[k] for k in ("size", "sha256")})
                mapping = json.loads(mapping_path.read_bytes())
                self.assertEqual(mapping["format"], "starfall-gacha-visual-assets")
                self.assertEqual(mapping["version"], version)
                self.assertEqual(mapping["schema_version"], 1)
                files = mapping["files"]
                self.assertEqual(len(files), count)
                self.assertTrue(previous_files.items() <= files.items())
                self.assertEqual(set(files), {v["path"] for v in mapping["characters"].values()})
                self.assertEqual(set(index["stages"][stage]["labels"]), set("ABCD"[:count]))
                for path, metadata in files.items():
                    self.assertEqual(path, f"assets/portraits/{metadata['sha256']}.png")
                    self.assertEqual(descriptor(folder / path), metadata)
                    self.assertEqual((folder / path).read_bytes()[:8], b"\x89PNG\r\n\x1a\n")
                    self.assertEqual(struct.unpack(">II", (folder / path).read_bytes()[16:24]), (48, 48))
                previous_files = files
            before = {p.relative_to(fixture): p.read_bytes() for p in fixture.rglob("*") if p.is_file()}
            second = subprocess.run(command, capture_output=True, text=True, check=False)
            self.assertEqual(second.returncode, 0, second.stderr)
            self.assertEqual(before, {p.relative_to(fixture): p.read_bytes() for p in fixture.rglob("*") if p.is_file()})

    def test_log_counts_status_and_response_bytes(self):
        from verify_requests import load_requests, verify

        with tempfile.TemporaryDirectory() as temp:
            log = Path(temp) / "access.log"
            log.write_text(
                '127.0.0.1 - - [test] "GET /qa/current.json HTTP/1.1" 200 11 "-" "Android"\n'
                '{"method":"GET","uri":"/qa/catalog-42.json","status":200,"bytes":12}\n'
                '{"method":"GET","uri":"/qa/visual-assets-42.json","status":200,"bytes":13}\n'
                '{"method":"GET","uri":"/qa/assets/portraits/c.png","status":200,"bytes":14}\n'
                '{"method":"GET","uri":"/other/assets/portraits/a.png","status":200,"bytes":22}\n',
                encoding="utf-8",
            )
            requests = load_requests(log, "/qa/")
            expected = {"current.json": (11, 1), "catalog-42.json": (12, 1),
                        "visual-assets-42.json": (13, 1), "assets/portraits/c.png": (14, 1)}
            self.assertEqual(verify(requests, expected)["assets/portraits/c.png"]["bytes"], 14)
            with self.assertRaisesRegex(ValueError, "request counts"):
                verify([*requests, requests[-1]], expected)
            bad_size = {**expected, "assets/portraits/c.png": (15, 1)}
            with self.assertRaisesRegex(ValueError, "expected GET 200 with 15 bytes"):
                verify(requests, bad_size)


if __name__ == "__main__":
    unittest.main()
