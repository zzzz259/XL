"""Exercise the visual candidate staging path without the live CDN."""

import hashlib
import json
import struct
import tempfile
import unittest
from dataclasses import replace
from io import BytesIO
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from PIL import Image

from server_app.catalog import AssetRef, BundleRef, CatalogIndex
from server_app.cdn import CategoryInfo, VersionCatalog
from server_app.config import load_config
from server_app.gacha_visual_publish import (
    publish_visual_candidate,
    read_visual_mapping,
)

ART = "Assets/Art/TextureSingle/BurstHead"
FGUI = "Assets/FairyGUI/UI"
GROUPS = ("characters", "fashions", "account_items")
CATALOG = VersionCatalog(125, (CategoryInfo("Arts", "a" * 32, 0, 7), CategoryInfo("Other", "b" * 32, 0, 8)))
BASELINE = {
    "BaseCard": '{[10000101] = {fashion_id = 12001011, star = 1}}',
    "BaseFashion": '{[12001011] = {card_id = 10000101, head_icon_rect = "CardHeadRect:Old"}}',
    "BaseItem": '{[21180001] = {type = 109, icon = "PlayerHeadFrame:Old"}}',
}


def _hash(number):
    return f"{number:032x}"


def _index(container, names, start):
    return CatalogIndex(
        tuple(BundleRef(f"bundle{number}", _hash(number), 1, ()) for number in range(start, start + len(names))),
        tuple(AssetRef(name, i, container) for i, name in enumerate(names)),
    )


def _package(package, name, *, rotated=False, rect=(0, 0, 320, 140), trim=None):
    short = lambda value: struct.pack(">H", value)
    integer = lambda value: struct.pack(">i", value)

    def string(value):
        data = value.encode()
        return short(len(data)) + data

    def item(kind, ident, label, filename, size):
        attrs = b"\0\0" if kind == 0 else b""
        data = bytes([kind]) + short(ident) + short(label) + short(65534) + short(filename)
        data += b"\1" + integer(size[0]) + integer(size[1]) + attrs + short(65534) + b"\0\0"
        return integer(len(data)) + data

    origin_size = trim[2:] if trim else rect[2:][::-1] if rotated else rect[2:]
    items = short(3) + item(4, 0, 65534, 1, (1024, 1024))
    items += item(4, 2, 65534, 3, (1024, 1024)) + item(0, 4, 5, 65534, origin_size)
    sprite = short(4) + short(2) + b"".join(map(integer, rect)) + bytes([rotated, bool(trim)])
    if trim:
        sprite += b"".join(map(integer, trim))
    sprites = short(1) + short(len(sprite)) + sprite
    strings = integer(6) + b"".join(map(string, ("a0", "atlas0.png", "a1", "atlas0_1.png", "im", name)))
    index = b"\x05\x01" + b"".join(map(short, (0, 12, 12 + len(items), 0, 12 + len(items) + len(sprites))))
    return b"FGUI" + integer(2) + b"\0" + string("test-pkg") + string(package) + bytes(20) + index + items + sprites + strings


class _Cdn:
    def __init__(self):
        self.catalogs = []
        self.bundles = []
        self.failing = None

    def download_category_catalog(self, category, digest, destination):
        self.catalogs.append(category)
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_bytes(b"fixture")
        return destination

    def download_bundle(self, digest, destination):
        self.bundles.append(digest)
        if digest == self.failing:
            raise OSError("failed to fetch the selected page")
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_bytes(b"UnityFS fixture")
        return destination


def _pointer(kind, **values):
    return SimpleNamespace(type=SimpleNamespace(name=kind), read=lambda: SimpleNamespace(**values))


class GachaVisualTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.lua = self.root / "lua"
        self.lua.mkdir()
        self.staging = self.root / "staging"
        self.visual = self.staging / "gacha_visual_assets"
        self.config = replace(load_config(Path(__file__).resolve().parents[1] / "config.toml.example"), data_dir=self.root)
        self.cdn = _Cdn()
        self.indexes = {}
        self.containers = {}
        self.loaded = []
        self.enterContext(patch("server_app.gacha_visual_assets.read_catalog", side_effect=self.read_catalog))
        self.enterContext(patch("server_app.gacha_visual_assets._load_bundle_container", side_effect=self.load_container))
        self.previous = self.enterContext(patch("server_app.gacha_visual_assets.read_active_visual", return_value=(None, None)))

    def write_lua(self, **overrides):
        for table, values in (BASELINE | overrides).items():
            (self.lua / f"{table}.lua").write_text(f"{table} = {values}\n", encoding="utf-8")

    def read_catalog(self, path, key):
        return self.indexes[path.name.split("_", 1)[0]]

    def load_container(self, root, dependencies):
        self.loaded.append((root.stem, [path.stem for path in dependencies]))
        return self.containers[root.stem].items()

    def stage(self):
        from server_app.gacha_visual_assets import stage_gacha_visual_assets

        return stage_gacha_visual_assets(self.lua, self.staging, CATALOG, self.config, self.cdn)

    def read_candidate(self):
        return json.loads((self.visual / "candidate.json").read_bytes())

    def assert_image(self, mapping, path, size):
        data = (self.visual / path).read_bytes()
        digest = hashlib.sha256(data).hexdigest()
        self.assertEqual(mapping["files"][path], {"sha256": digest, "size": len(data)})
        self.assertEqual(Path(path).stem, digest)
        with Image.open(self.visual / path) as image:
            self.assertEqual(image.size, size)
            return image.convert("RGBA")

    def install_package(self, package, name, page, start, **geometry):
        names = [f"{package}_fui.bytes", f"{package}_atlas0.png", f"{package}_atlas0_1.png"]
        self.indexes["other"] = _index(FGUI, names, start)
        self.containers[_hash(start)] = {
            f"{FGUI}/{names[0]}".lower(): _pointer("TextAsset", m_Script=_package(package, name, **geometry))
        }
        self.containers[_hash(start + 2)] = {
            f"{FGUI}/{names[2]}".lower(): _pointer("Texture2D", image=page)
        }

    def test_frozen_manifest_has_all_bundled_ids_and_no_candidates_fetch_nothing(self):
        manifest = Path(__file__).resolve().parents[2] / "xl_gacha_android" / "web" / "assets" / "manifest.json"
        bundled = json.loads(manifest.read_bytes())
        baseline_file = Path(__file__).resolve().parents[1] / "assets" / "gacha_visual_baseline_0_4_2.json"
        frozen = json.loads(baseline_file.read_bytes())
        self.assertEqual(frozen["resource_version"], bundled["resource_version"])
        source_sha = hashlib.sha256(manifest.read_text(encoding="utf-8").encode("utf-8")).hexdigest()
        self.assertEqual(frozen["source"], {"tag": "gacha-v0.4.2", "sha256": source_sha})
        for group in GROUPS:
            self.assertEqual(set(frozen[group]), set(bundled[group]))
            path_key = "rect_path" if group == "fashions" else "path"
            for key, entry in frozen[group].items():
                self.assertEqual(entry[path_key], bundled[group][key][path_key])
                self.assertEqual(entry["sha256"], bundled["files"][entry[path_key]])
        self.write_lua()
        self.assertEqual(self.stage(), ())
        self.assertEqual(self.cdn.bundles, [])
        self.assertEqual(self.cdn.catalogs, [])
        self.assertFalse((self.visual / "candidate.json").exists())

    def test_fashion_downloads_exact_package_then_only_the_selected_page(self):
        self.write_lua(BaseFashion='{[19000001]={card_id=10000101, head_icon_rect="CardHeadRect:Wanted"}}')
        self.install_package("CardHeadRect", "Wanted", Image.new("RGBA", (140, 320), "red"), 10,
                             rotated=True, rect=(0, 0, 139, 318), trim=(1, 1, 320, 140))
        self.assertEqual(self.stage(), ())
        self.assertEqual(self.cdn.catalogs, ["Other"])
        self.assertEqual(self.cdn.bundles, [_hash(10), _hash(12)])
        mapping = self.read_candidate()["mapping"]
        self.assertEqual(mapping["version"], "125")
        entry = mapping["fashions"]["19000001"]
        self.assertNotIn("path", entry)
        self.assertEqual(entry["crop_rect"], [91, 23, 88, 88])
        image = self.assert_image(mapping, entry["rect_path"], (320, 140))
        self.assertEqual(image.getpixel((0, 0)), (0, 0, 0, 0))
        self.assertEqual(image.getpixel((1, 1)), (255, 0, 0, 255))

    def test_package_and_shared_page_decode_once_with_their_exact_dependencies(self):
        self.write_lua(
            BaseFashion='{[19000001]={card_id=10000101, head_icon_rect="CardHeadRect:Wanted"}}',
            BaseItem='{[19000002]={type=108, icon="CardHeadRect:Wanted"}}',
        )
        self.install_package("CardHeadRect", "Wanted", Image.new("RGBA", (320, 140), "red"), 10)
        index = self.indexes["other"]
        bundles = (
            index.bundles[0],
            replace(index.bundles[1], deps=(5,)),
            replace(index.bundles[2], deps=(3,)),
            BundleRef("page_dep", _hash(13), 1, (4,)),
            BundleRef("transitive_dep", _hash(14), 1, (3,)),
            BundleRef("irrelevant_dep", _hash(15), 1, ()),
        )
        self.indexes["other"] = replace(index, bundles=bundles)
        self.assertEqual(self.stage(), ())
        self.assertEqual(self.cdn.bundles, [_hash(10), _hash(12), _hash(13), _hash(14)])
        self.assertEqual(self.loaded, [(_hash(10), []), (_hash(12), [_hash(13), _hash(14)])])
        mapping = self.read_candidate()["mapping"]
        self.assertEqual(mapping["fashions"]["19000001"]["rect_path"], mapping["account_items"]["19000002"]["path"])
        self.assertEqual(len(mapping["files"]), 1)
        public = self.root / "public"
        descriptor = publish_visual_candidate(public, self.visual, "125", None)
        self.assertIsNotNone(descriptor)
        self.assertEqual(read_visual_mapping(public, descriptor), mapping)

    def test_portrait_uses_burst_then_falls_back_to_the_configured_square(self):
        self.write_lua(
            BaseCard='{[19000001]={fashion_id=19000002,star=5,name=function() return T(19) end},'
                     '[19000003]={fashion_id=12001011,star=4}}',
            BaseFashion='{[19000002]={card_id=19000001,head_icon_burst="BurstHead_A.png"},'
                        '[12001011]={card_id=19000003,head_icon_burst="BurstHead_Missing.png",'
                        'head_icon_square="CardHeadSquare:Wanted"}}',
            BaseWord_cn='{[19]={name="New card"}}',
        )
        self.indexes["arts"] = _index(ART, ["BurstHead_A.png"], 20)
        self.containers[_hash(20)] = {
            f"{ART}/BurstHead_A.png".lower(): _pointer("Texture2D", image=Image.new("RGBA", (32, 32), "blue")),
            "assets/art/texturesingle/ui/bursthead_a.png": _pointer("Texture2D", image=Image.new("RGBA", (32, 32), "yellow")),
        }
        self.install_package("CardHeadSquare", "Wanted", Image.new("RGBA", (140, 270), "green"), 10, rect=(0, 0, 140, 270))
        self.assertTrue(any("19000002" in warning for warning in self.stage()))
        self.assertEqual(self.cdn.bundles, [_hash(20), _hash(10), _hash(12)])
        mapping = self.read_candidate()["mapping"]
        burst, square = (mapping["characters"][item] for item in ("19000001", "19000003"))
        self.assertEqual(burst["rarity"], 5)
        self.assertEqual(burst["name"], "New card")
        self.assertEqual(self.assert_image(mapping, burst["path"], (256, 256)).getpixel((128, 128)), (0, 0, 255, 255))
        self.assertEqual(self.assert_image(mapping, square["path"], (256, 256)).getpixel((128, 128)), (0, 128, 0, 255))

    def test_account_items_reuse_verified_remote_hashes_and_keep_the_existing_mapping(self):
        self.write_lua(BaseItem='{[19000001]={type=109,icon="PlayerHeadFrame:Wanted",effect_value=1234},'
                                 '[19000002]={type=109,icon="Missing:Skipped"}}')
        red = Image.new("RGBA", (32, 32), "red")
        output = BytesIO()
        red.save(output, format="PNG")
        raw = output.getvalue()
        digest = hashlib.sha256(raw).hexdigest()
        path = f"assets/account/{digest}.png"
        old = {
            "format": "starfall-gacha-visual-assets",
            "schema_version": 1,
            "version": "124",
            "account_items": {"19000002": {"path": path, "type": 109}},
            "files": {path: {"sha256": digest, "size": len(raw)}},
        }
        self.previous.return_value = (old, "2" * 64)
        self.install_package("PlayerHeadFrame", "Wanted", red, 10, rect=(0, 0, 32, 32))
        self.assertEqual(self.stage(), ())
        candidate = self.read_candidate()
        self.assertEqual(candidate["base_sha256"], "2" * 64)
        mapping = candidate["mapping"]
        self.assertEqual(mapping["account_items"]["19000002"], old["account_items"]["19000002"])
        self.assertEqual(mapping["account_items"]["19000001"], {"path": path, "type": 109, "effect_value": "1234"})
        self.assertEqual(mapping["files"], old["files"])
        self.assertEqual(list(self.visual.glob("assets/**/*")), [])

    def test_ambiguous_exact_asset_is_reported_without_guessing(self):
        self.write_lua(BaseItem='{[19000001]={type=108,icon="CardHeadRect:Wanted"}}')
        self.indexes["other"] = _index(FGUI, ["CardHeadRect_fui.bytes", "CardHeadRect_fui.bytes"], 10)
        self.assertTrue(any("ambiguous" in warning and "19000001" in warning for warning in self.stage()))
        self.assertEqual(self.cdn.bundles, [])
        self.assertFalse((self.visual / "candidate.json").exists())

    def test_out_of_bounds_sprite_retains_last_good_manifest(self):
        self.write_lua(BaseItem='{[19000001]={type=108,icon="CardHeadRect:Wanted"}}')
        self.install_package("CardHeadRect", "Wanted", Image.new("RGBA", (320, 140), "red"), 10,
                             rect=(1, 0, 320, 140))
        self.assertTrue(any("bounds" in warning for warning in self.stage()))
        self.assertFalse((self.visual / "candidate.json").exists())

    def test_page_download_failure_discards_already_rendered_candidates(self):
        self.write_lua(
            BaseCard='{[19000001]={fashion_id=19000002,star=5}}',
            BaseFashion='{[19000002]={card_id=19000001,head_icon_burst="BurstHead_A.png",'
                        'head_icon_rect="CardHeadRect:Wanted"}}',
        )
        self.indexes["arts"] = _index(ART, ["BurstHead_A.png"], 20)
        self.containers[_hash(20)] = {
            f"{ART}/BurstHead_A.png".lower(): _pointer("Texture2D", image=Image.new("RGBA", (32, 32), "blue")),
        }
        self.install_package("CardHeadRect", "Wanted", Image.new("RGBA", (320, 140), "red"), 10)
        self.cdn.failing = _hash(12)
        self.assertTrue(any("failed to fetch" in warning for warning in self.stage()))
        self.assertFalse(self.visual.exists())

    def test_visual_healthcheck_loads_the_release_baseline_and_image_tools(self):
        from server_app.gacha_visual_assets import validate_visual_runtime

        self.assertIsNone(validate_visual_runtime())


if __name__ == "__main__":
    unittest.main()
