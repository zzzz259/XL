"""Stage visual mappings for Lua IDs absent from the 0.4.2 APK and active update."""

from __future__ import annotations

import hashlib
import io
import json
import logging
import re
import sys
import tempfile
from collections import defaultdict
from copy import deepcopy
from dataclasses import dataclass
from pathlib import Path

import UnityPy
from PIL import features

from .catalog import AssetRef, CatalogIndex, read_catalog
from .cdn import CdnClient, VersionCatalog
from .character_adapter import _tool_app_root
from .config import ServerConfig
from .extractor import _script_bytes, repair_bundle_header
from .gacha_adapter import parse_lua_table
from .gacha_visual_publish import MAX_IMAGE, VISUAL_GROUPS, read_active_visual

LOGGER = logging.getLogger(__name__)
BASELINE = Path(__file__).resolve().parents[1] / "assets" / "gacha_visual_baseline_0_4_2.json"
ART_DIRECTORY = "Assets/Art/TextureSingle/BurstHead"
FGUI_DIRECTORY = "Assets/FairyGUI/UI"
FGUI_REFERENCE = re.compile(r"([A-Za-z0-9_]+):([A-Za-z0-9_]+)")
BURST_REFERENCE = re.compile(r"BurstHead_[A-Za-z0-9_]+\.png", re.IGNORECASE)
MD5 = re.compile(r"[a-f0-9]{32}")
SHA256 = re.compile(r"[a-f0-9]{64}")
VISUAL_ID = re.compile(r"[1-9][0-9]{0,15}")


class UnavailableSource(ValueError):
    """The exact asset could not be selected."""


class MissingSource(UnavailableSource):
    """The exact asset is absent from the game index or configuration."""


def _load_baseline() -> dict:
    baseline = json.loads(BASELINE.read_bytes())
    source = baseline["source"]
    if source["tag"] != "gacha-v0.4.2" or not SHA256.fullmatch(source["sha256"]):
        raise ValueError("invalid source of the frozen visual baseline")
    for group in VISUAL_GROUPS:
        rows = baseline[group]
        if not isinstance(rows, dict) or not rows:
            raise ValueError(f"empty frozen visual baseline group: {group}")
        path_key = "rect_path" if group == "fashions" else "path"
        for item_id, entry in rows.items():
            if (not VISUAL_ID.fullmatch(item_id) or not SHA256.fullmatch(entry["sha256"])
                    or not re.fullmatch(r"assets/(?:portraits|account|account-rect)/[A-Za-z0-9_]+\.(?:png|webp)", entry[path_key])):
                raise ValueError(f"invalid frozen visual baseline entry: {group}/{item_id}")
    return baseline


def _load_image_tools():
    tool_root = str(_tool_app_root())
    if tool_root not in sys.path:
        sys.path.insert(0, tool_root)
    from app.features.preview import fgui_atlas, gacha_image_processing

    return fgui_atlas, gacha_image_processing


def validate_visual_runtime() -> None:
    """Check the bundled baseline and image tools without fetching resources."""
    _load_baseline()
    _load_image_tools()
    if not features.check("webp"):
        raise RuntimeError("Pillow WebP support is required for gacha portraits")


def _unity_path(value: str) -> str:
    return value.replace("\\", "/").strip("/").casefold()


def _load_bundle_container(root: Path, dependencies: tuple[Path, ...]):
    environment = UnityPy.load(str(repair_bundle_header(root)))
    for path in dependencies:
        environment.load_file(str(repair_bundle_header(path)), is_dependency=True)
    return environment.container.items()


class BundleAssets:
    """Load category indexes on demand and retrieve only the requested asset's closure."""

    def __init__(self, version_catalog: VersionCatalog, staging: Path, config: ServerConfig, client: CdnClient):
        self.categories = {entry.name: entry.hash for entry in version_catalog.categories}
        self.catalog_dir = config.data_dir / "catalogs"
        self.bundle_dir = staging / "bundles" / "GachaVisual"
        self.decrypt_key = config.assetbundle_key
        self.client = client
        self.indexes = {}

    def _category(self, category: str) -> tuple[CatalogIndex, dict[str, set[AssetRef]]]:
        if category not in self.indexes:
            digest = self.categories.get(category, "")
            if not MD5.fullmatch(digest):
                raise ValueError(f"missing or invalid visual category index: {category}")
            path = self.catalog_dir / f"{category.lower()}_{digest}.bundle"
            cached = path.is_file()
            if not cached:
                self.client.download_category_catalog(category, digest, path)
            index = read_catalog(path, self.decrypt_key)
            references = defaultdict(set)
            for asset in index.assets:
                if asset.container and asset.bundle_index >= 0:
                    references[_unity_path(f"{asset.container}/{asset.name}")].add(asset)
            self.indexes[category] = (index, references)
            LOGGER.info("stage=gacha_visual_assets category=%s index=%s bytes=%d cached=%s",
                        category, digest, path.stat().st_size, cached)
        return self.indexes[category]

    def read(self, category: str, directory: str, filename: str, object_type: str):
        index, references = self._category(category)
        exact_path = _unity_path(f"{directory}/{filename}")
        matches = references.get(exact_path, ())
        if not matches:
            raise MissingSource(f"missing {category} asset {directory}/{filename}")
        if len(matches) != 1:
            raise UnavailableSource(f"ambiguous {category} asset {directory}/{filename}")
        root = next(iter(matches)).bundle_index
        paths = self._download_closure(index, root)
        LOGGER.info("stage=gacha_visual_assets asset=%s category=%s type=%s root=%s dependencies=%s",
                    exact_path, category, object_type, paths[0].stem, [path.stem for path in paths[1:]])
        matches = [
            pointer for path, pointer in _load_bundle_container(paths[0], paths[1:])
            if _unity_path(path) == exact_path and pointer.type.name == object_type
        ]
        if len(matches) > 1:
            raise UnavailableSource(f"ambiguous Unity {object_type} at {exact_path}")
        if not matches:
            raise ValueError(f"indexed Unity {object_type} could not be decoded at {exact_path}")
        source = matches[0].read()
        LOGGER.info("stage=gacha_visual_assets asset=%s unity_object=%s", exact_path, getattr(source, "m_Name", ""))
        return source

    def _download_closure(self, index: CatalogIndex, root: int) -> tuple[Path, ...]:
        pending, visited, paths = [root], set(), {}
        while pending:
            bundle_index = pending.pop()
            if bundle_index in visited:
                continue
            visited.add(bundle_index)
            if not 0 <= bundle_index < len(index.bundles):
                raise ValueError(f"invalid visual bundle index {bundle_index}")
            bundle = index.bundles[bundle_index]
            if not MD5.fullmatch(bundle.hash):
                raise ValueError(f"invalid visual bundle digest at index {bundle_index}")
            if bundle.hash not in paths:
                path = self.bundle_dir / f"{bundle.hash}.bundle"
                cached = path.is_file()
                if not cached:
                    self.client.download_bundle(bundle.hash, path)
                LOGGER.info("stage=gacha_visual_assets bundle=%s bytes=%d cached=%s",
                            bundle.hash, bundle.size, cached)
                paths[bundle.hash] = path
            pending.extend(reversed(bundle.deps))
        return tuple(paths.values())


@dataclass
class Request:
    group: str
    item_id: str
    entry: dict
    fgui: object = None
    burst: object = None
    card_id: str | None = None

    @property
    def label(self) -> str:
        return f"{self.group}/{self.item_id}"


def _lua_rows(lua_dir: Path, name: str) -> dict:
    path = next((item for item in lua_dir.iterdir() if item.name.casefold() == f"{name}.lua".casefold()), None)
    if path is None:
        raise FileNotFoundError(f"missing {name}.lua")
    return {
        str(key): record for key, record in parse_lua_table(path.read_text(encoding="utf-8-sig"), name).items()
        if isinstance(record, dict) and VISUAL_ID.fullmatch(str(key)) and int(key) <= 2**53 - 1
    }


def _requests(lua_dir: Path, baseline: dict, previous: dict):
    cards = _lua_rows(lua_dir, "BaseCard")
    fashions = _lua_rows(lua_dir, "BaseFashion")
    accounts = _lua_rows(lua_dir, "BaseItem")
    remaining = {
        group: rows.keys() - set(baseline[group]) - previous.get(group, {}).keys()
        for group, rows in zip(VISUAL_GROUPS, (cards, fashions, accounts), strict=True)
    }
    words = {}
    if any(isinstance(cards[item_id].get("name"), dict) and "__translation__" in cards[item_id]["name"]
           for item_id in remaining["characters"]):
        words = _lua_rows(lua_dir, "BaseWord_cn")
    for item_id in sorted(remaining["characters"]):
        card = cards[item_id]
        fashion = fashions.get(str(card.get("fashion_id")), {})
        if str(fashion.get("card_id")) != item_id:
            fashion = {}
        rarity = card.get("star")
        entry = {"rarity": rarity} if type(rarity) is int and 1 <= rarity <= 5 else {}
        name = card.get("name")
        if isinstance(name, dict):
            name = words.get(str(name.get("__translation__")), {}).get("name")
        if (isinstance(name, str) and name.strip() and len(name.encode("utf-16-le")) <= 240
                and all(not (ord(char) < 32 or 127 <= ord(char) <= 159) for char in name)):
            entry["name"] = name.strip()
        yield Request("characters", item_id, entry, fashion.get("head_icon_square"), fashion.get("head_icon_burst"))
    for item_id in sorted(remaining["fashions"]):
        fashion = fashions[item_id]
        if not fashion.get("card_id"):
            continue
        yield Request("fashions", item_id, {}, fashion.get("head_icon_rect"), card_id=str(fashion["card_id"]))
    for item_id in sorted(remaining["account_items"]):
        item = accounts[item_id]
        item_type = item.get("type")
        if item_type not in (108, 109):
            continue
        entry = {"type": item_type}
        effect = item.get("effect_value")
        if effect is not None:
            if not re.fullmatch(r"[0-9]{1,10}", str(effect)):
                raise ValueError(f"invalid effect_value for account item {item_id}")
            entry["effect_value"] = str(effect)
        yield Request("account_items", item_id, entry, item.get("icon"))


class MappingBuilder:
    def __init__(self, stage: Path, previous: dict, version: str, processing):
        self.stage = stage
        self.mapping = deepcopy(previous)
        self.mapping.update(format="starfall-gacha-visual-assets", schema_version=1, version=version)
        self.files = self.mapping.setdefault("files", {})
        self.existing = {record["sha256"]: path for path, record in sorted(self.files.items())}
        self.processing = processing
        self.new_fashions = {}
        self.added = 0

    def add(self, request: Request, source, *, square_fallback=False):
        processing = self.processing
        if not processing.alpha_bbox(source):
            raise ValueError(f"empty decoded image for {request.label}")
        entry = request.entry
        group = request.group
        raw = io.BytesIO()
        path_key = "path"
        if group == "characters":
            image = processing.prepare_portrait(source, square_fallback=square_fallback)
            processing.save_lossless_webp(image, raw)
            directory, extension = "portraits", "webp"
            entry.update(crop_rect=processing.PORTRAIT_CROP, source_size=image.size)
        else:
            directory, extension = "account", "png"
            if group == "fashions" or entry.get("type") == 108:
                if source.size != processing.ACCOUNT_HEAD_SOURCE_SIZE:
                    raise ValueError(f"unexpected head rect size for {request.label}: {source.size}")
                entry.update(crop_rect=processing.ACCOUNT_HEAD_CROP, source_size=source.size)
            if group == "fashions":
                directory, path_key = "account-rect", "rect_path"
                self.new_fashions[request.item_id] = request.card_id
            source.save(raw, "PNG")
        data = raw.getvalue()
        if not 0 < len(data) <= MAX_IMAGE:
            raise ValueError(f"invalid encoded image size for {request.label}: {len(data)}")
        digest = hashlib.sha256(data).hexdigest()
        relative = self.existing.get(digest)
        if relative is None:
            relative = f"assets/{directory}/{digest}.{extension}"
            path = self.stage / relative
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(data)
            self.files[relative] = {"sha256": digest, "size": len(data)}
            self.existing[digest] = relative
        entry[path_key] = relative
        self.mapping.setdefault(group, {})[request.item_id] = entry
        self.added += 1
        LOGGER.info("stage=gacha_visual_assets item=%s path=%s bytes=%d", request.label, relative, len(data))

    def write(self, base_sha256: str | None, destination: Path):
        remote_portraits = self.mapping.get("characters", {})
        for item_id, card_id in self.new_fashions.items():
            portrait = remote_portraits.get(card_id, {}).get("path")
            if portrait:
                self.mapping["fashions"][item_id]["path"] = portrait
        self.stage.mkdir(parents=True, exist_ok=True)
        candidate = self.stage / "candidate.json"
        temporary = candidate.with_suffix(".json.part")
        temporary.write_text(
            json.dumps({"base_sha256": base_sha256, "mapping": self.mapping},
                       sort_keys=True, ensure_ascii=False, allow_nan=False, separators=(",", ":")),
            encoding="utf-8",
        )
        temporary.replace(candidate)
        self.stage.replace(destination)


def _package_pages(sources, fgui, package_name, requests, report):
    filename = f"{package_name}_fui.bytes"
    package = fgui.UIPackage()
    data = _script_bytes(sources.read("Other", FGUI_DIRECTORY, filename, "TextAsset"))
    if len(data) <= 9 or data[8] != 0:
        raise ValueError(f"invalid or compressed FGUI descriptor {filename}")
    prefix = filename.removesuffix(".bytes")
    package.load_package(fgui.ByteBuffer(data), prefix)
    if package.name != package_name:
        raise ValueError(f"FGUI package name mismatch: {package.name!r} for {filename}")
    pages = defaultdict(list)
    for sprite_name, request in requests:
        try:
            sprite, size = _package_sprite(fgui, package, sprite_name)
            atlas_file = sprite.atlas.file
            page_suffix = atlas_file.removeprefix(prefix + "_")
            if page_suffix == atlas_file or not re.fullmatch(r"atlas[0-9_]+\.png", page_suffix):
                raise ValueError(f"invalid FGUI page {atlas_file!r} in {filename}")
            rect = sprite.rect
            LOGGER.info("stage=gacha_visual_assets item=%s sprite=%s:%s page=%s rect=%s rotated=%s offset=%s size=%s",
                        request.label, package_name, sprite_name, page_suffix, (rect.x, rect.y, rect.width, rect.height),
                        sprite.rotated, (sprite.offset.x, sprite.offset.y), size)
            pages[f"{package_name}_{page_suffix}"].append((sprite, size, request))
        except UnavailableSource as error:
            report(request, error)
    return pages


def _package_sprite(fgui, package, name):
    items = [item for item in package.get_items() if item.name == name and item.type == fgui.PackageItemType.Image]
    if not items:
        raise MissingSource(f"missing exact FGUI sprite {package.name}:{name}")
    if len(items) != 1:
        raise UnavailableSource(f"ambiguous FGUI sprite {package.name}:{name}")
    item = items[0]
    if item.id in package.duplicate_sprite_ids or sum(other.id == item.id for other in package.get_items()) != 1:
        raise UnavailableSource(f"ambiguous FGUI sprite ID for {package.name}:{name}")
    sprite = package.sprites.get(item.id)
    if sprite is None or sprite.atlas.type != fgui.PackageItemType.Atlas:
        raise ValueError(f"invalid FGUI atlas reference for {package.name}:{name}")
    if sum(other.id == sprite.atlas.id for other in package.get_items()) != 1:
        raise UnavailableSource(f"ambiguous FGUI atlas ID for {package.name}:{name}")
    return sprite, (item.width, item.height)


def _render_requests(requests, sources, builder, fgui_atlas, warnings):
    def report(request, error):
        warnings.append(f"gacha visual assets: {request.label}: {error}"[:500])

    packages = defaultdict(list)
    for request in requests:
        try:
            if isinstance(request.burst, str) and BURST_REFERENCE.fullmatch(request.burst):
                try:
                    source = sources.read("Arts", ART_DIRECTORY, request.burst, "Texture2D")
                except MissingSource as error:
                    LOGGER.info("stage=gacha_visual_assets source=%s fallback=CardHeadSquare reason=%s", request.label, error)
                else:
                    with source.image as image:
                        builder.add(request, image)
                    continue
            reference = FGUI_REFERENCE.fullmatch(request.fgui) if isinstance(request.fgui, str) else None
            if not reference or (request.group == "characters" and reference[1] != "CardHeadSquare"):
                raise MissingSource(f"no exact configured source (burst={request.burst!r}, fgui={request.fgui!r})")
            LOGGER.info("stage=gacha_visual_assets item=%s source=%s", request.label, request.fgui)
            packages[reference[1]].append((reference[2], request))
        except UnavailableSource as error:
            report(request, error)
    for package, request_group in packages.items():
        try:
            pages = _package_pages(sources, fgui_atlas, package, request_group, report)
        except UnavailableSource as error:
            for _, request in request_group:
                report(request, error)
            continue
        for filename, sprites in pages.items():
            try:
                texture = sources.read("Other", FGUI_DIRECTORY, filename, "Texture2D")
            except UnavailableSource as error:
                for _, _, request in sprites:
                    report(request, error)
                continue
            with texture.image as atlas:
                for sprite, size, request in sprites:
                    try:
                        with fgui_atlas.crop_atlas_sprite(atlas, sprite) as image:
                            if image.size != size:
                                raise ValueError(f"FGUI sprite size mismatch: {image.size} != {size}")
                            builder.add(request, image, square_fallback=request.group == "characters")
                    except ValueError as error:
                        raise ValueError(f"{request.label} ({request.fgui}): {error}") from error
            del texture


def _stage(decoded_lua, staging, version_catalog, config, client, warnings):
    previous, base_sha256 = read_active_visual(config.data_dir / "gacha_config")
    previous = previous or {}
    version = str(version_catalog.timestamp)
    if previous and int(previous["version"]) >= version_catalog.timestamp:
        return
    baseline = _load_baseline()
    requests = list(_requests(decoded_lua, baseline, previous))
    if not requests:
        return
    fgui_atlas, processing = _load_image_tools()
    sources = BundleAssets(version_catalog, staging, config, client)
    staging.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix=".gacha-visual-", dir=staging) as temporary:
        builder = MappingBuilder(Path(temporary) / "candidate", previous, version, processing)
        _render_requests(requests, sources, builder, fgui_atlas, warnings)
        if builder.added:
            builder.write(base_sha256, staging / "gacha_visual_assets")


def stage_gacha_visual_assets(
    decoded_lua: Path, staging: Path, version_catalog: VersionCatalog, config: ServerConfig, client: CdnClient
) -> tuple[str, ...]:
    """Leave the active mapping in place if fetching or decoding an indexed asset fails."""
    warnings = []
    try:
        _stage(decoded_lua, staging, version_catalog, config, client, warnings)
    except Exception as error:  # noqa: BLE001 - Keep the independent data update usable.
        warnings.append(f"gacha visual assets: {error}"[:500])
    for warning in warnings:
        LOGGER.warning("stage=gacha_visual_assets version=%s %s", version_catalog.timestamp, warning)
    return tuple(warnings)
