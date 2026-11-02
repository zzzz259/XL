"""可复现素材生成器与已提交视觉产物的 unittest 验收。"""

from __future__ import annotations

import hashlib
import importlib.util
import json
import os
import statistics
import sys
import unittest
from pathlib import Path

from PIL import Image, ImageChops

ROOT = Path(__file__).resolve().parents[1]
WEB = ROOT / "web"
ASSETS = WEB / "assets"
MANIFEST_PATH = ASSETS / "manifest.json"
MANIFEST = json.loads(MANIFEST_PATH.read_text(encoding="utf-8"))

_SPEC = importlib.util.spec_from_file_location(
    "prepare_visual_assets", ROOT / "scripts" / "prepare_visual_assets.py"
)
pva = importlib.util.module_from_spec(_SPEC)
sys.modules.setdefault("prepare_visual_assets", pva)
_SPEC.loader.exec_module(pva)

PACK_ROOT = Path(os.environ.get("STARFALL_ASSET_PACK", pva.DEFAULT_PACK))
requires_source = unittest.skipUnless(
    PACK_ROOT.is_dir(), "原始素材包不存在；设置 STARFALL_ASSET_PACK 后执行源图对照"
)
MASCOT_SETS = ("idle", "jump_all", "relive", "die")
MASCOT_TOTALS = {"idle": 1366, "jump_all": 1699, "relive": 1366, "die": 1333}


def sha256(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def alpha_bbox(image):
    return pva.alpha_bbox(image)


def visible_pixels_equal(left, right):
    if left.size != right.size:
        return False
    left = left.convert("RGBA")
    right = right.convert("RGBA")
    if ImageChops.difference(left.getchannel("A"), right.getchannel("A")).getbbox() is not None:
        return False
    difference = ImageChops.difference(left.convert("RGB"), right.convert("RGB"))
    visible = left.getchannel("A").point(lambda alpha: 255 if alpha else 0)
    masked = Image.composite(difference, Image.new("RGB", left.size), visible)
    return masked.getbbox() is None


def trimmed_reference(image):
    box = alpha_bbox(image)
    if box is None:
        return Image.new("RGBA", (256, 256))
    margin = 4
    box = (
        max(0, box[0] - margin), max(0, box[1] - margin),
        min(image.width, box[2] + margin), min(image.height, box[3] + margin),
    )
    cropped = image.crop(box)
    scale = min(256 / cropped.width, 256 / cropped.height)
    size = (max(1, round(cropped.width * scale)), max(1, round(cropped.height * scale)))
    resized = cropped.resize(size, Image.Resampling.LANCZOS)
    output = Image.new("RGBA", (256, 256))
    output.paste(resized, ((256 - size[0]) // 2, (256 - size[1]) // 2))
    return output


def mascot_webp(name):
    if name == "idle":
        return WEB / MANIFEST["mascot"]["idle"]["path"]
    for reaction in MANIFEST["mascot"]["reactions"]:
        if reaction["name"] == name:
            return WEB / reaction["path"]
    raise AssertionError(f"manifest 缺少 mascot 动画 {name}")


def source_mascot(name):
    return PACK_ROOT / "previews" / "mascot" / f"minigame017_gugu-{name}.webp"


GAME_OUTPUT = Path(os.environ.get("STARFALL_GAME_OUTPUT", ROOT.parent / "xl_updata_tool" / "output"))


@unittest.skipUnless((GAME_OUTPUT / "lua" / "134377281279792758" / "BaseCard.lua").is_file(), "最新导出 Lua 不存在")
class LatestGameSourceTests(unittest.TestCase):
    @requires_source
    def test_latest_tables_add_available_assets_and_report_unexported_references(self):
        source = pva.load_game_catalogs(PACK_ROOT, GAME_OUTPUT)
        self.assertEqual(source["resource_version"], "134377281279792758")
        self.assertEqual(source["source_counts"], {"characters": 109, "fashions": 322, "account_items": 101})
        self.assertEqual(len(source["characters"]), 109)
        self.assertEqual(len(source["fashions"]), 316)
        self.assertEqual(len(source["account_items"]), 100)
        self.assertEqual(source["characters"]["10000227"]["name"], "菲欧")
        self.assertTrue(Path(source["characters"]["10000227"]["bursthead"]).is_file())
        self.assertTrue(Path(source["account_items"]["21181240"]["path"]).is_file())
        self.assertNotIn("21181283", source["account_items"], "没有导出的框不能伪装成别的框")
        missing = source["unavailable_assets"]
        self.assertEqual({entry["id"] for entry in missing}, {"12002251", "12002252", "12002253", "12002261", "12002262", "12002263", "21181283"})
        for entry in missing:
            self.assertTrue(entry["path"].endswith(".png"))
            self.assertFalse((GAME_OUTPUT / entry["path"]).exists())
            self.assertIn(entry["type"], ("fashion_head", 109))


class CharacterTests(unittest.TestCase):
    def test_manifest_matches_repository_character_catalog(self):
        catalog = json.loads(
            (ROOT.parent / "xl_gacha_web" / "data" / "catalog.json").read_text(encoding="utf-8")
        )
        self.assertEqual(len(MANIFEST["characters"]), 109)
        self.assertEqual(set(MANIFEST["characters"]), set(catalog["cards"]))

    def test_every_character_maps_to_initial_portrait(self):
        for item_id, entry in MANIFEST["characters"].items():
            with self.subTest(item_id=item_id):
                self.assertEqual(entry["path"], f"assets/portraits/{item_id}.webp")
                self.assertTrue((WEB / entry["path"]).is_file())

    def test_portraits_expose_a_head_crop_within_the_real_source_canvas(self):
        for item_id, entry in MANIFEST["characters"].items():
            with self.subTest(item_id=item_id):
                self.assertEqual(entry.get("crop_rect"), [38, 0, 180, 180])
                with Image.open(WEB / entry["path"]) as image:
                    self.assertEqual(entry.get("source_size"), list(image.size))

    def test_portrait_count_on_disk(self):
        self.assertEqual(len(list((ASSETS / "portraits").glob("*.webp"))), 109)

    def test_portraits_are_256_lossless_webp(self):
        for item_id, entry in MANIFEST["characters"].items():
            with self.subTest(item_id=item_id):
                path = WEB / entry["path"]
                riff = pva.parse_webp_riff(path)
                self.assertEqual(riff["canvas"], (256, 256))
                self.assertTrue(riff["lossless"])
                with Image.open(path) as image:
                    self.assertEqual(image.size, (256, 256))


@requires_source
class CharacterSourceTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.catalog = pva.load_game_catalogs(PACK_ROOT, GAME_OUTPUT)["characters"]

    def test_source_catalog_mapping(self):
        self.assertEqual(set(MANIFEST["characters"]), set(self.catalog))

    def test_portraits_trim_blank_edges_without_cutting_content(self):
        checked = 0
        for item_id, entry in self.catalog.items():
            if not entry.get("bursthead"):
                continue
            with self.subTest(item_id=item_id):
                output = Image.open(WEB / MANIFEST["characters"][item_id]["path"]).convert("RGBA")
                reference = Image.open(PACK_ROOT / entry["bursthead"]).convert("RGBA")
                self.assertTrue(visible_pixels_equal(output, trimmed_reference(reference)))
            checked += 1
        self.assertEqual(checked, 107)

    def test_special_square_portraits_are_not_cropped(self):
        for item_id in ("10000199", "10000200"):
            with self.subTest(item_id=item_id):
                entry = self.catalog[item_id]
                self.assertIsNone(entry["bursthead"])
                source = Image.open(PACK_ROOT / entry["square"]).convert("RGBA")
                output = Image.open(WEB / MANIFEST["characters"][item_id]["path"]).convert("RGBA")
                self.assertEqual(source.size, (140, 270))
                source_box = alpha_bbox(source)
                padded = Image.new("RGBA", (270, 270))
                dx = round((270 - (source_box[2] - source_box[0])) / 2) - source_box[0]
                padded.paste(source, (dx, 0))
                self.assertTrue(visible_pixels_equal(output, trimmed_reference(padded)))


class FashionTests(unittest.TestCase):
    def test_fashions_separate_gacha_portraits_from_account_rects(self):
        portrait_paths = {entry["path"] for entry in MANIFEST["characters"].values()}
        self.assertEqual(len(MANIFEST["fashions"]), 316)
        rect_paths = {entry.get("rect_path") for entry in MANIFEST["fashions"].values()}
        self.assertEqual(len(rect_paths), 316)
        for fashion_id, entry in MANIFEST["fashions"].items():
            with self.subTest(fashion_id=fashion_id):
                self.assertIn(entry["path"], portrait_paths)
                self.assertTrue((WEB / entry["path"]).is_file())
                self.assertTrue(entry["rect_path"].startswith("assets/account-rect/"))
                with Image.open(WEB / entry["rect_path"]) as image:
                    self.assertEqual(image.size, (320, 140))
        self.assertFalse((ASSETS / "fashions").exists())

    def test_player_heads_use_original_fgui_loader_crop_for_both_id_families(self):
        entries = list(MANIFEST["fashions"].values()) + [
            entry for entry in MANIFEST["account_items"].values() if entry["type"] == 108
        ]
        for entry in entries:
            with self.subTest(path=entry["path"]):
                self.assertEqual(entry.get("crop_rect"), [91, 23, 88, 88])
                source = entry.get("rect_path", entry["path"])
                with Image.open(WEB / source) as image:
                    self.assertEqual(entry.get("source_size"), list(image.size))
                    self.assertEqual(image.size, (320, 140))

    @requires_source
    def test_fashions_map_to_owning_character_and_real_account_rect(self):
        catalog = pva.load_game_catalogs(PACK_ROOT, GAME_OUTPUT)["fashions"]
        self.assertEqual(set(MANIFEST["fashions"]), set(catalog))
        for fashion_id, entry in catalog.items():
            with self.subTest(fashion_id=fashion_id):
                card_id = str(entry["card_id"])
                self.assertEqual(
                    MANIFEST["fashions"][fashion_id]["path"],
                    f"assets/portraits/{card_id}.webp",
                )
                rect_name = Path(entry["paths"]["head_icon_rect"]).name
                self.assertEqual(
                    MANIFEST["fashions"][fashion_id]["rect_path"],
                    f"assets/account-rect/{rect_name}",
                )


class StaticAssetTests(unittest.TestCase):
    def test_account_brand_and_ui_mappings(self):
        self.assertEqual(len(MANIFEST["account_items"]), 100)
        paths = {entry["path"] for entry in MANIFEST["account_items"].values()}
        self.assertEqual(len(paths), 99)
        for path in paths:
            self.assertTrue(path.startswith("assets/account/"), path)
            self.assertTrue((WEB / path).is_file(), path)
        self.assertEqual(
            MANIFEST["brand"],
            {
                "logo": "assets/brand/starfall-logo.png",
                "noise": "assets/ui/perlinNoise.png",
                "primary_button": "assets/ui/primary-button.png",
            },
        )
        for key in ("logo", "noise", "primary_button"):
            self.assertTrue((WEB / MANIFEST["brand"][key]).is_file(), key)

    @requires_source
    def test_ui_files_match_source(self):
        pairs = {
            "assets/ui/perlinNoise.png": "images/masks/perlinNoise.png",
            "assets/ui/primary-button.png": pva.UI_PRIMARY_BUTTON_SRC,
            "assets/brand/starfall-logo.png": "images/brand/starfall-logo.png",
        }
        for output, source in pairs.items():
            with self.subTest(output=output):
                self.assertEqual(sha256(WEB / output), sha256(PACK_ROOT / source))


class ManifestTests(unittest.TestCase):
    def test_files_count_matches_available_latest_assets(self):
        self.assertEqual(len(MANIFEST["files"]), 109 + 316 + 99 + 1 + 2 + 5)

    def test_hashes_match_disk(self):
        for relative, expected in MANIFEST["files"].items():
            with self.subTest(path=relative):
                path = WEB / relative
                self.assertTrue(path.is_file())
                self.assertEqual(sha256(path), expected)

    def test_no_unlisted_output_files(self):
        listed = set(MANIFEST["files"])
        for path in ASSETS.rglob("*"):
            if path.is_file() and path.name != "manifest.json":
                relative = path.relative_to(WEB).as_posix()
                self.assertIn(relative, listed)


class MascotTests(unittest.TestCase):
    def test_all_frames_use_864_canvas(self):
        for name in MASCOT_SETS:
            with self.subTest(name=name):
                path = mascot_webp(name)
                with Image.open(path) as image:
                    self.assertEqual(image.size, (864, 864))
                    self.assertEqual(image.n_frames, len(pva.parse_webp_riff(path)["frames"]))
                    for index in range(image.n_frames):
                        image.seek(index)
                        self.assertEqual(image.size, (864, 864))

    def test_duration_totals_and_loop(self):
        for name, total in MASCOT_TOTALS.items():
            with self.subTest(name=name):
                riff = pva.parse_webp_riff(mascot_webp(name))
                durations = [frame["duration"] for frame in riff["frames"]]
                self.assertTrue(durations)
                self.assertLessEqual(set(durations), {33, 34})
                self.assertEqual(sum(durations), total)
                self.assertEqual(riff["loop"], 0)

    @requires_source
    def test_source_durations_are_preserved(self):
        for name in MASCOT_SETS:
            with self.subTest(name=name):
                output = pva.parse_webp_riff(mascot_webp(name))
                source = pva.parse_webp_riff(source_mascot(name))
                self.assertEqual(
                    [frame["duration"] for frame in output["frames"]],
                    [frame["duration"] for frame in source["frames"]],
                )

    def test_baseline_centering_and_complete_frames(self):
        for name in MASCOT_SETS:
            with self.subTest(name=name):
                frames = pva.decode_frames(mascot_webp(name))
                boxes = [alpha_bbox(frame) for frame in frames]
                bottoms = [box[3] for box in boxes]
                centers_x = [(box[0] + box[2]) / 2 for box in boxes]
                ground = max(bottoms)
                landing = [index for index, bottom in enumerate(bottoms) if bottom >= ground - 2]
                base_bottom = statistics.median(bottoms[index] for index in landing)
                base_center = statistics.median(centers_x[index] for index in landing)
                self.assertLessEqual(abs(base_bottom - 848), 1)
                self.assertLessEqual(abs(base_center - 432), 2)
                self.assertGreaterEqual(min(box[1] for box in boxes), 0)
                self.assertLessEqual(max(bottoms), 864)

    def test_poster_matches_idle_first_frame(self):
        poster_path = WEB / MANIFEST["mascot"]["idle"]["poster"]
        self.assertEqual(poster_path.name, "minigame017_gugu-idle.png")
        poster = Image.open(poster_path).convert("RGBA")
        idle_first = pva.decode_frames(mascot_webp("idle"))[0]
        self.assertEqual(poster.size, (864, 864))
        self.assertTrue(visible_pixels_equal(poster, idle_first))

    def test_manifest_mascot_durations(self):
        mascot = MANIFEST["mascot"]
        self.assertEqual(mascot["idle"]["durationMs"], 1366)
        self.assertEqual(mascot["idle"]["path"], "assets/mascot/minigame017_gugu-idle.webp")
        self.assertEqual(mascot["idle"]["poster"], "assets/mascot/minigame017_gugu-idle.png")
        self.assertEqual(
            [(reaction["name"], reaction["durationMs"]) for reaction in mascot["reactions"]],
            [("jump_all", 1699), ("relive", 1366), ("die", 1333)],
        )

    @requires_source
    def test_character_content_is_not_scaled(self):
        for name in MASCOT_SETS:
            with self.subTest(name=name):
                output = [alpha_bbox(frame) for frame in pva.decode_frames(mascot_webp(name))]
                source = [alpha_bbox(frame) for frame in pva.decode_frames(source_mascot(name))]
                self.assertEqual(
                    [(box[2] - box[0], box[3] - box[1]) for box in output],
                    [(box[2] - box[0], box[3] - box[1]) for box in source],
                )


if __name__ == "__main__":
    unittest.main()
