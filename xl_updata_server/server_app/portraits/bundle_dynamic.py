"""Canonical-to-operational dynamic profile binding, without importing media."""

from pathlib import Path


def operational_profile(value, roots, paths, hashes, aliases):
    from app.platform.portrait_dynamic_profile import (
        FILE_FIELDS,
        MASKED,
        SCHEMA,
    )

    from .bundle import BundleError, portable_path

    profile = value["dynamic_profile"]
    if (
        type(profile) is not dict
        or profile.get("font_mode") not in ("directory", "built-ins")
        or set(profile)
        != {
            "schema",
            "font_mode",
            *FILE_FIELDS,
            "share_tree",
            *(["font_tree"] if profile.get("font_mode") == "directory" else []),
        }
        or profile["schema"] != SCHEMA
    ):
        raise BundleError("unsupported canonical dynamic profile")
    for root in roots.values():
        if any(
            root == Path(mask) or root.is_relative_to(Path(mask)) for mask in MASKED
        ):
            raise BundleError("immutable root hidden by private namespace")
    result = {"schema": SCHEMA, "font_mode": profile["font_mode"]}
    for field in FILE_FIELDS:
        ref = profile[field]
        if (
            type(ref) is not list
            or len(ref) != 2
            or ref[0]
            != (
                "display" if field in ("egl_vendor_json", "tree_manifest") else "native"
            )
        ):
            raise BundleError("fixed dynamic file role required")
        key = tuple(ref)
        if key not in paths:
            raise BundleError("dynamic input absent from inventory")
        if field not in ("egl_vendor_json", "tree_manifest"):
            if ref not in value["descriptor_groups"]["x_libraries"]:
                raise BundleError(
                    "dynamic Mesa provider missing from display/managed loader"
                )
            entry = next(e for e in value["entries"] if (e["role"], e["path"]) == key)
            if entry["kind"] != "elf":
                raise BundleError("actual ELF dynamic provider required")
        result[field] = {"path": str(paths[key]), "sha256": hashes[key]}
    for field in (
        "share_tree",
        *(["font_tree"] if profile["font_mode"] == "directory" else []),
    ):
        ref = profile[field]
        if type(ref) is not list or len(ref) != 2 or ref[0] != "display":
            raise BundleError("fixed display resource role required")
        path = roots["display"] / portable_path(ref[1])
        if path in aliases or not path.is_dir() or path.resolve() != path:
            raise BundleError("real display selection directory required")
        result[field] = str(path)
    if profile["font_mode"] == "built-ins":
        entries = {(e["role"], e["path"]): e for e in value["entries"]}

        def abi(key):
            return (
                value["host_files"][key[1]]["abi"]
                if key[0] == "host"
                else entries[tuple(key)].get("abi", {})
            )

        xvfb = value["descriptor_groups"]["xvfb"][0]
        providers = [
            key
            for key in value["descriptor_groups"]["x_libraries"]
            if abi(key).get("soname") == "libXfont2.so.2"
        ]
        if "libXfont2.so.2" not in abi(xvfb).get("needed", []) or len(providers) != 1:
            raise BundleError(
                "built-in fonts require actual Xvfb NEEDED libXfont2 provider"
            )
        for name, key in (("xvfb", xvfb), ("builtin_font_provider", providers[0])):
            result[name] = {
                "path": str(paths[tuple(key)]),
                "sha256": hashes[tuple(key)],
            }
    return result


def verify_dynamic(
    value,
    roots,
    paths,
    hashes,
    aliases,
    operational,
    *,
    loader_orders,
    host_alias_targets,
):
    from app.platform.portrait_dynamic_profile import verify_profile

    from .bundle import MAX_JSON, BundleError, _read_bounded, canonical

    expected = operational_profile(value, roots, paths, hashes, aliases)
    if operational != expected:
        raise BundleError("dynamic profile differs from canonical selection")
    # GLVND loads these vendors by SONAME rather than DT_NEEDED. Verify the
    # first actual candidate under both stage environments and each possible
    # verified native caller's RPATH/RUNPATH, including explicit host aliases.
    for field in ("glx_vendor", "egl_vendor"):
        selected = Path(expected[field]["path"])
        for directories in loader_orders:
            candidate = next(
                (
                    directory / selected.name
                    for directory in directories
                    if (directory / selected.name).is_file()
                ),
                None,
            )
            if (
                candidate is None
                or host_alias_targets.get(candidate, candidate) != selected
            ):
                raise BundleError("unproven dynamic vendor loader selection")
    share = Path(expected["share_tree"])
    tree_manifest = {
        "schema": 1,
        "files": sorted(
            [
                {"path": path.relative_to(share).as_posix(), "sha256": hashes[key]}
                for key, path in paths.items()
                if path.is_relative_to(share)
            ],
            key=lambda row: row["path"],
        ),
        "aliases": sorted(
            [
                {
                    "path": path.relative_to(share).as_posix(),
                    "target": target.relative_to(share).as_posix(),
                }
                for path, target in aliases.items()
                if path.is_relative_to(share)
            ],
            key=lambda row: row["path"],
        ),
    }
    if _read_bounded(Path(expected["tree_manifest"]["path"]), MAX_JSON) != canonical(
        tree_manifest
    ):
        raise BundleError(
            "display tree manifest differs from canonical entries/aliases"
        )
    try:
        compact = verify_profile(operational)
    except ValueError as exc:
        raise BundleError("invalid actual dynamic profile") from exc
    # Supported Xvfb build must carry its fixed compiled XKB/helper locations;
    # these are supplied bytes, not provenance labels or the system executable.
    path = paths[tuple(value["descriptor_groups"]["xvfb"][0])]
    import mmap

    with (
        path.open("rb") as stream,
        mmap.mmap(stream.fileno(), 0, access=mmap.ACCESS_READ) as raw,
    ):
        helper = raw.find(b"/usr/bin/xkbcomp\0") >= 0 or (
            raw.find(b"/usr/bin\0") >= 0 and raw.find(b'"%s%sxkbcomp" -w %d ') >= 0
        )
        if raw.find(b"/usr/share/X11/xkb\0") < 0 or not helper:
            raise BundleError("unsupported Xvfb compiled resource paths")
    return compact
