"""Generate cumulative v1 visual snapshots using only procedural PNGs."""

import argparse
import hashlib
import json
import struct
import zlib
from collections import Counter
from pathlib import Path

BUNDLED_VERSION = 134379197371350448
COLORS = ((222, 63, 71), (59, 161, 96), (52, 128, 233), (222, 156, 44))


def json_bytes(value):
    return (json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")) + "\n").encode("utf-8")


def blob(raw):
    return {"sha256": hashlib.sha256(raw).hexdigest(), "size": len(raw)}


def chunk(kind, data):
    return struct.pack(">I", len(data)) + kind + data + struct.pack(">I", zlib.crc32(kind + data))


def portrait(number, color):
    size = 48
    rows = []
    for y in range(size):
        row = bytearray([0])
        for x in range(size):
            border = min(x, y, size - 1 - x, size - 1 - y) < 3
            stripe = 10 <= x < 10 + number * 7 and (x - 10) % 7 < 4 and 15 <= y < 33
            row.extend((24, 28, 36) if border else (251, 252, 255) if stripe else color)
        rows.append(row)
    header = struct.pack(">IIBBBBB", size, size, 8, 2, 0, 0, 0)
    return b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", header) + chunk(b"IDAT", zlib.compress(b"".join(rows), 9)) + chunk(b"IEND", b"")


def write_fixed(path, data):
    if path.exists():
        if path.read_bytes() != data:
            raise ValueError(f"Existing fixture has different bytes: {path}. Use a fresh output directory.")
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("xb") as stream:
        stream.write(data)


def generate(catalog, demo, out, first_version, four_groups=False):
    floor = max(BUNDLED_VERSION, int(catalog["version"]))
    if first_version <= floor or first_version + 2 >= 10**20:
        raise ValueError(f"V1 version must exceed {floor} and leave room for V2/V3 under 20 digits.")
    candidates = Counter(
        int(drop["item_id"]) for record in demo["records"] for drop in record["drops"]
        if (card := catalog["cards"].get(str(drop["item_id"]))) and card[1] == 5
    )
    ids = sorted(candidates, key=lambda item: (-candidates[item], item))[:4]
    if len(ids) != 4:
        raise ValueError("The synthetic demo must reference at least four characters ranked 5 in the catalog.")
    entries = [("characters", item_id, "path", "portraits", {
        "name": catalog["cards"][str(item_id)][0], "rarity": 5,
    }) for item_id in ids]
    if four_groups:
        geometry = {"source_size": [48, 48], "crop_rect": [4, 4, 40, 40]}
        entries[1:] = [
            ("fashions", 80990001, "rect_path", "account-rect", geometry),
            ("account_items", 80990002, "path", "account", {"type": 108, **geometry}),
            ("account_items", 80990003, "path", "account", {"type": 109}),
        ]
    images = {}
    for number, (label, entry, color) in enumerate(zip("ABCD", entries, COLORS), start=1):
        group, item_id, key, folder, extra = entry
        raw = portrait(number, color)
        descriptor = blob(raw)
        path = f"assets/{folder}/{descriptor['sha256']}.png"
        images[label] = {"item_id": str(item_id), "path": path, **descriptor,
                         "width": 48, "height": 48, "raw": raw, "group": group,
                         "entry_key": key, "entry": {key: path, **extra}}

    stages = {}
    for offset, count in enumerate((2, 3, 4)):
        stage = f"V{offset + 1}"
        version = str(first_version + offset)
        folder = out / stage
        labels = "ABCD"[:count]
        files = {}
        mappings = {}
        for label in labels:
            image = images[label]
            item_id, path = image["item_id"], image["path"]
            write_fixed(folder / path, image["raw"])
            files[path] = {key: image[key] for key in ("sha256", "size")}
            mappings.setdefault(image["group"], {})[item_id] = image["entry"]

        mapping_name = f"visual-assets-{version}.json"
        mapping = json_bytes({"format": "starfall-gacha-visual-assets", "schema_version": 1,
                              "version": version, "files": files, **mappings})
        write_fixed(folder / mapping_name, mapping)
        visual = {"schema_version": 1, "version": version, "file": mapping_name, **blob(mapping)}
        catalog_name = f"catalog-{version}.json"
        catalog_raw = json_bytes({**catalog, "version": version, "visual_assets": visual})
        write_fixed(folder / catalog_name, catalog_raw)
        manifest = {"format": "starfall-gacha-catalog-manifest", "schema_version": 1,
                    "version": version, "file": catalog_name, **blob(catalog_raw)}
        current_raw = json_bytes(manifest)
        write_fixed(folder / "current.json", current_raw)
        stages[stage] = {"version": version, "labels": labels, "current": blob(current_raw),
                         "catalog": {"file": catalog_name, **blob(catalog_raw)}, "visual": visual}

    index = {"input_catalog_version": catalog["version"], "stages": stages,
             "images": {label: {k: v for k, v in image.items() if k != "raw"}
                        for label, image in images.items()}}
    write_fixed(out / "qa-index.json", json_bytes(index))
    return index


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--catalog", type=Path, required=True, help="Bundled catalog.json")
    parser.add_argument("--demo", type=Path, required=True, help="Existing synthetic demo report")
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--first-version", type=int)
    args = parser.parse_args()
    catalog = json.loads(args.catalog.read_bytes())
    demo = json.loads(args.demo.read_bytes())
    version = args.first_version if args.first_version is not None else max(BUNDLED_VERSION, int(catalog["version"])) + 1
    try:
        index = generate(catalog, demo, args.output, version)
    except ValueError as error:
        parser.error(str(error))
    for stage, recorded in index["stages"].items():
        print(f"{stage} {recorded['version']} {recorded['labels']} {recorded['visual']['sha256']}")


if __name__ == "__main__":
    main()
