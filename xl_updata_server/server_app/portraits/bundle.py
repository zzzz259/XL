"""Read-only, fail-closed verification of explicitly supplied portrait bundles.

No discovery on PATH, installation, descriptor generation or media imports.
Roots and pins are trusted immutable deployment configuration, never job data.
"""

import hashlib
import json
import mmap
import os
import re
import struct
import sys
from dataclasses import dataclass
from pathlib import Path, PurePosixPath

ROLES = frozenset(("server", "tool", "python", "dotnet", "java", "native", "display"))
INVENTORY = "runtime/portrait/inventory.json"
MAX_JSON = 2 * 1024**2
MAX_FILES = 50000
MAX_FILE = 512 * 1024**2
GROUPS = frozenset(
    (
        "dotnet",
        "app_files",
        "native_files",
        "xvfb",
        "xkbcomp",
        "x_libraries",
        "runtime_files",
    )
)


class BundleError(ValueError):
    """Missing, ambiguous or unsupported bundle evidence; activation is unready."""


def _read_bounded(path, limit):
    with path.open("rb") as stream:
        raw = stream.read(limit + 1)
    if len(raw) > limit:
        raise BundleError("file byte bound")
    return raw


def canonical(value):
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    ).encode("utf-8")


def strict_json(raw, *, canonical_required=True):
    def pairs(rows):
        value = {}
        for key, item in rows:
            if key in value:
                raise BundleError("duplicate JSON key")
            value[key] = item
        return value

    try:
        if len(raw) > MAX_JSON:
            raise BundleError("JSON byte bound")
        value = json.loads(
            raw.decode("utf-8"),
            object_pairs_hook=pairs,
            parse_constant=lambda _: (_ for _ in ()).throw(
                BundleError("nonfinite JSON")
            ),
        )
        if canonical_required and canonical(value) != raw:
            raise BundleError("noncanonical JSON")
        return value
    except (UnicodeError, ValueError, TypeError, RecursionError) as exc:
        raise BundleError("invalid strict JSON") from exc


def portable_path(value):
    if (
        not isinstance(value, str)
        or not 0 < len(value) <= 512
        or any(c in value for c in "\\:\x00")
    ):
        raise BundleError("invalid portable path")
    if any(ord(c) < 32 or c in '<>"|?*' for c in value):
        raise BundleError("invalid portable characters")
    parts = value.split("/")
    if any(
        re.fullmatch(r"(CON|PRN|AUX|NUL|COM[1-9]|LPT[1-9])(?:\..*)?", p, re.IGNORECASE)
        for p in parts
    ):
        raise BundleError("reserved portable name")
    if (
        any(p in ("", ".", "..") or p.rstrip(" .") != p for p in parts)
        or not value.isascii()
    ):
        raise BundleError("invalid portable path")
    return value


def _file(root, name):
    portable_path(name)
    path = root
    for part in PurePosixPath(name).parts:
        path = path / part
        if path.is_symlink():
            raise BundleError("symlink input forbidden")
    if not path.is_file() or not path.resolve().is_relative_to(root.resolve()):
        raise BundleError("missing or escaping input")
    return path


def _hash(path, size, expected):
    if (
        type(size) is not int
        or not 0 <= size <= MAX_FILE
        or not re.fullmatch("[0-9a-f]{64}", expected)
    ):
        raise BundleError("invalid size/hash identity")
    digest = hashlib.sha256()
    count = 0
    with path.open("rb") as stream:
        while block := stream.read(1024 * 1024):
            count += len(block)
            if count > size:
                raise BundleError("file exceeds declared bound")
            digest.update(block)
    if count != size or digest.hexdigest() != expected:
        raise BundleError("actual bytes mismatch")


def _binary_identity(path, parser):
    if not 0 < path.stat().st_size <= MAX_FILE:
        raise BundleError("binary byte bound")
    with (
        path.open("rb") as stream,
        mmap.mmap(stream.fileno(), 0, access=mmap.ACCESS_READ) as data,
    ):
        return parser(data)


def managed_identity(data):
    """Require CLR IL or the evidenced .NET 8 Linux-x64 ReadyToRun format."""
    try:
        if data[:2] != b"MZ" or len(data) < 64:
            raise BundleError("managed PE required")
        pe = struct.unpack_from("<I", data, 60)[0]
        if data[pe : pe + 4] != b"PE\0\0":
            raise BundleError("managed PE signature")
        machine, count, _, _, _, optional_size, _ = struct.unpack_from(
            "<HHIIIHH", data, pe + 4
        )
        optional = pe + 24
        magic = struct.unpack_from("<H", data, optional)[0]
        directory = optional + (96 if magic == 0x10B else 112 if magic == 0x20B else 0)
        if (
            machine not in (0x14C, 0x8664, 0xFD1D)
            or directory == optional
            or not 0 < count <= 96
        ):
            raise BundleError("unsupported managed PE")
        if optional_size < directory - optional + 15 * 8:
            raise BundleError("missing CLR directory")
        rva, size = struct.unpack_from("<II", data, directory + 14 * 8)
        if size < 72:
            raise BundleError("missing CLR header")

        def mapped(rva, length):
            for index in range(count):
                item = optional + optional_size + index * 40
                virtual, address, raw_size, offset = struct.unpack_from(
                    "<IIII", data, item + 8
                )
                if address <= rva and rva + length <= address + min(virtual, raw_size):
                    result = offset + rva - address
                    if result + length <= len(data):
                        return result
            raise BundleError("unmapped CLR metadata")

        for i in range(count):
            section = optional + optional_size + i * 40
            virtual_size, address, raw_size, raw_offset = struct.unpack_from(
                "<IIII", data, section + 8
            )
            if address <= rva and rva + 72 <= address + min(virtual_size, raw_size):
                position = raw_offset + rva - address
                flags = struct.unpack_from("<I", data, position + 16)[0]
                metadata_rva, metadata_size = struct.unpack_from(
                    "<II", data, position + 8
                )
                metadata = mapped(metadata_rva, metadata_size)
                if (
                    metadata_size < 16
                    or data[metadata : metadata + 4] != b"BSJB"
                    or flags & 0x10
                ):
                    raise BundleError("invalid CLR metadata signature")
                if machine in (0x14C, 0x8664) and flags & 1:
                    return {"format": "CLR-IL"}
                if machine == 0xFD1D and magic == 0x20B:
                    native_rva, native_size = struct.unpack_from(
                        "<II", data, position + 64
                    )
                    native = mapped(native_rva, native_size)
                    if (
                        native_size >= 16
                        and data[native : native + 4] == b"RTR\0"
                        and struct.unpack_from("<HH", data, native + 4)
                        in ((9, 0), (9, 1))
                    ):
                        return {"format": "CLR-R2R-linux-x86_64"}
        raise BundleError("native or invalid CLR image")
    except (struct.error, IndexError) as exc:
        raise BundleError("malformed managed PE") from exc


def _origin_needed_suffix(name):
    """Recognize only bounded, anchored loader ORIGIN direct dependencies."""
    for prefix in ("$ORIGIN/", "${ORIGIN}/"):
        if name.startswith(prefix):
            suffix = name[len(prefix) :]
            parts = suffix.split("/")
            if (
                len(name) > 512
                or len(parts) > 64
                or any(not re.fullmatch(r"[A-Za-z0-9_.+~-]+", part) for part in parts)
                or parts[-1] in (".", "..")
            ):
                raise BundleError("nonportable ELF dependency")
            return suffix
    return None


def elf_identity(data):
    """Parse ELF64 LE x86-64 section/dynamic tables; stripped tables fail closed.

    ABI strings are conservatively scanned too: provided *or* required newer
    symbol versions reject, avoiding an unsupported loader claiming readiness.
    """
    try:
        if len(data) < 64 or data[:7] != b"\x7fELF\x02\x01\x01":
            raise BundleError("unsupported ELF format")
        header = struct.unpack_from("<HHIQQQIHHHHHH", data, 16)
        if header[0] not in (2, 3) or header[1] != 62 or header[2] != 1:
            raise BundleError("unsupported ELF architecture")
        interpreter = None
        phoff, phsize, phcount = header[4], header[8], header[9]
        if phcount:
            if phsize != 56 or phcount > 1024 or phoff + phsize * phcount > len(data):
                raise BundleError("invalid ELF program headers")
            for i in range(phcount):
                ph = struct.unpack_from("<IIQQQQQQ", data, phoff + i * phsize)
                if ph[0] == 3:
                    if (
                        interpreter is not None
                        or not 1 < ph[5] <= 512
                        or ph[2] + ph[5] > len(data)
                    ):
                        raise BundleError("invalid ELF interpreter")
                    raw = data[ph[2] : ph[2] + ph[5]]
                    if raw[-1:] != b"\0" or b"\0" in raw[:-1]:
                        raise BundleError("invalid ELF interpreter string")
                    interpreter = raw[:-1].decode("ascii")
                    if not interpreter.startswith("/"):
                        raise BundleError("absolute ELF interpreter required")
        offset, entsize, count = header[5], header[10], header[11]
        if entsize != 64 or not 0 < count <= 8192 or offset + count * 64 > len(data):
            raise BundleError("ELF section evidence unavailable")
        sections = [
            struct.unpack_from("<IIQQQQIIQQ", data, offset + i * 64)
            for i in range(count)
        ]
        needed, soname, rpath, runpath = [], None, [], []

        def string(blob, index):
            end = blob.find(b"\0", index)
            if index < 0 or end < index or end - index > 512:
                raise BundleError("invalid ELF string")
            return blob[index:end].decode("ascii")

        for section in sections:
            if section[1] != 6:
                continue
            start, size, link = section[4], section[5], section[6]
            if link >= count or start + size > len(data) or size % 16:
                raise BundleError("invalid ELF dynamic table")
            strings = sections[link]
            blob = data[strings[4] : strings[4] + strings[5]]
            if len(blob) != strings[5]:
                raise BundleError("truncated ELF strings")
            for pos in range(start, start + size, 16):
                tag, value = struct.unpack_from("<qQ", data, pos)
                if tag in (1, 14):
                    name = string(blob, value)
                    direct = _origin_needed_suffix(name) if tag == 1 else None
                    if direct is None and (
                        not re.fullmatch(r"[A-Za-z0-9_.+~-]+", name)
                        or name in (".", "..")
                    ):
                        raise BundleError("nonportable ELF dependency")
                    if tag == 1:
                        needed.append(name)
                    else:
                        soname = name
                if tag in (15, 29):
                    parts = string(blob, value).split(":")
                    if len(parts) > 64:
                        raise BundleError("ELF loader path bound")
                    if tag == 15:
                        rpath.extend(parts)
                    else:
                        runpath.extend(parts)
        versions = {}
        for family, ceiling in ((b"GLIBC", (2, 28)), (b"GLIBCXX", (3, 4, 25))):
            found = [
                tuple(map(int, match.split(b".")))
                for match in re.findall(family + rb"_([0-9]+(?:\.[0-9]+)+)", data)
            ]
            highest = max(found, default=())
            if highest > ceiling:
                raise BundleError("target ABI floor exceeded")
            versions[family.decode()] = ".".join(map(str, highest))
        return {
            "machine": "x86_64",
            "class": 64,
            "needed": sorted(set(needed)),
            "soname": soname,
            "interpreter": interpreter,
            "rpath": rpath,
            "runpath": runpath,
            **versions,
        }
    except (struct.error, UnicodeError, IndexError) as exc:
        raise BundleError("malformed ELF") from exc


def loader_directories(path, abi, roots, configured, defaults=()):
    """Resolve explicit loader order; never consult ambient cwd/PATH/cache."""
    result = list(configured) if abi.get("runpath") else []
    for value in abi.get("runpath", []) or abi.get("rpath", []):
        if not value or value == "." or len(value) > 512:
            raise BundleError("unsafe ELF loader directory")
        expanded = value.replace("${ORIGIN}", str(path.parent)).replace(
            "$ORIGIN", str(path.parent)
        )
        if "$" in expanded:
            raise BundleError("unknown ELF loader variable")
        candidate = Path(expanded)
        if not candidate.is_absolute():
            # Relative ELF paths are cwd-dependent; there is no immutable cwd
            # guarantee at every dlopen, so require an explicit ORIGIN token.
            raise BundleError("cwd-relative ELF loader path")
        candidate = candidate.resolve()
        if not candidate.is_dir() or not any(
            candidate.is_relative_to(root.resolve()) for root in roots
        ):
            raise BundleError("ELF loader escape")
        if candidate not in result:
            result.append(candidate)
    for candidate in (*configured, *defaults):
        candidate = Path(candidate)
        if (
            not candidate.is_absolute()
            or not candidate.is_dir()
            or not any(
                candidate.resolve().is_relative_to(root.resolve()) for root in roots
            )
        ):
            raise BundleError("unverified configured loader directory")
        if candidate not in result:
            result.append(candidate)
    return result


def directory_aliases(roots, rows):
    """Bind explicit same-root directory links without enumerating them twice."""
    if not isinstance(rows, list) or len(rows) > 128:
        raise BundleError("directory alias bound")
    aliases = {}
    for row in rows:
        if (
            set(row) != {"role", "path", "target_role", "target_path"}
            or row["role"] != row["target_role"]
            or row["role"] not in roots
        ):
            raise BundleError("same-root directory alias required")
        root = Path(roots[row["role"]])
        path, target = (
            root / portable_path(row["path"]),
            root / portable_path(row["target_path"]),
        )
        if path in aliases or not path.is_symlink():
            raise BundleError("unique actual directory link required")
        link = path.readlink()
        actual = Path(
            os.path.abspath(link if link.is_absolute() else path.parent / link)
        )
        if actual != target or not target.is_relative_to(root):
            raise BundleError("directory alias target mismatch")
        aliases[path] = target
    for start, target in aliases.items():
        path = target
        seen = {start}
        for _ in range(32):
            if path in seen:
                raise BundleError("directory alias cycle")
            seen.add(path)
            if path not in aliases:
                if path.is_symlink() or path.resolve() != path or not path.is_dir():
                    raise BundleError("directory alias must end in real immutable tree")
                break
            path = aliases[path]
        else:
            raise BundleError("directory alias chain bound")
    return aliases


def declared_path(path, aliases):
    """Resolve only declared file/directory aliases, rejecting every unknown hop."""
    path = Path(os.path.abspath(path))
    for _ in range(32):
        for ancestor in reversed((path, *path.parents)):
            if ancestor.is_symlink():
                if ancestor not in aliases:
                    raise BundleError("undeclared loaded-module alias")
                path = aliases[ancestor] / path.relative_to(ancestor)
                break
        else:
            return path
    raise BundleError("loaded-module alias chain bound")


def _same_dso_origin(loading_path, actual_path, aliases):
    """File aliases retain their loading directory; directory aliases traverse."""
    return declared_path(loading_path.parent, aliases) == actual_path.parent


def verify_inventory(
    roots, entries, *, source_scopes=None, aliases=None, file_aliases=()
):
    if set(roots) != ROLES or not isinstance(entries, list) or len(entries) > MAX_FILES:
        raise BundleError("invalid roots/entry count")
    roots = {key: Path(value) for key, value in roots.items()}
    aliases = aliases or {}
    if any(
        not p.is_absolute() or not p.is_dir() or p.is_symlink() or p.resolve() != p
        for p in roots.values()
    ):
        raise BundleError("explicit real absolute roots required")
    resolved_roots = [p.resolve() for p in roots.values()]
    if any(
        a.is_relative_to(b)
        for i, a in enumerate(resolved_roots)
        for j, b in enumerate(resolved_roots)
        if i != j
    ):
        raise BundleError("overlapping immutable roots")
    identities, paths, binaries = [], {}, {}
    folded = set()
    for entry in entries:
        if (
            not isinstance(entry, dict)
            or not {"role", "path", "size", "sha256", "kind"} <= entry.keys()
        ):
            raise BundleError("entry identity incomplete")
        role, name = entry["role"], portable_path(entry["path"])
        if role not in ROLES or (role == "tool" and name == INVENTORY):
            raise BundleError("invalid entry role/self identity")
        key = (role, name)
        if (role, name.casefold()) in folded:
            raise BundleError("duplicate portable input")
        folded.add((role, name.casefold()))
        path = _file(roots[role], name)
        _hash(path, entry["size"], entry["sha256"])
        with path.open("rb") as stream:
            magic = stream.read(4)
        kind = entry["kind"]
        allowed = {"role", "path", "size", "sha256", "kind"}
        if kind == "elf":
            allowed |= {"platform", "provenance", "abi"}
        elif kind == "managed":
            allowed |= {"platform", "provenance"}
        if set(entry) != allowed:
            raise BundleError("unknown or missing entry evidence")
        if kind not in ("source", "data", "managed", "elf"):
            raise BundleError("unknown file kind")
        if magic == b"\x7fELF" or kind == "elf":
            if (
                kind != "elf"
                or not isinstance(entry.get("provenance"), str)
                or not entry["provenance"]
            ):
                raise BundleError("native provenance missing")
            identity = _binary_identity(path, elf_identity)
            if entry.get("abi") != identity or entry.get("platform") != "linux-x86_64":
                raise BundleError("native ABI evidence mismatch")
            binaries[key] = identity
        elif kind == "managed":
            if (
                entry["platform"] != "net8.0"
                or not isinstance(entry["provenance"], str)
                or not entry["provenance"]
            ):
                raise BundleError("managed provenance missing")
            _binary_identity(path, managed_identity)
        elif magic[:2] == b"MZ":
            raise BundleError("unclassified PE binary")
        paths[key] = path
        identities.append(key)
    if identities != sorted(identities):
        raise BundleError("entries must be ordered")
    # Every active import root is a dedicated complete tree. source_scopes
    # records the minimum recipe; it never exempts siblings from verification.
    for role, root in roots.items():
        scopes = list((source_scopes or {}).get(role, [""]))
        for target in aliases.values():
            if (
                target.is_relative_to(root)
                and target != root
                and target.relative_to(root).as_posix() not in scopes
            ):
                scopes.append(target.relative_to(root).as_posix())
        if (
            not isinstance(scopes, list)
            or not 0 < len(scopes) <= 128
            or len(set(scopes)) != len(scopes)
        ):
            raise BundleError("invalid closure scopes")
        for scope in scopes:
            if scope:
                portable_path(scope)
                if not (root / scope).is_dir() or (root / scope).is_symlink():
                    raise BundleError("missing closure directory")
        if role in ("server", "tool"):
            scopes = [""]
        known_directories = {
            parent
            for key, path in paths.items()
            if key[0] == role
            for parent in path.parents
            if parent.is_relative_to(root)
        }
        if role == "tool":
            known_directories.update((root / INVENTORY).parents)
        for scope in scopes:
            base = root / portable_path(scope) if scope else root
            if (
                base.is_symlink()
                or not base.resolve().is_relative_to(root.resolve())
                or not base.is_dir()
            ):
                raise BundleError("missing closure directory")
            for count, path in enumerate(base.rglob("*"), 1):
                if count > MAX_FILES:
                    raise BundleError("enumeration bound")
                if path.suffix in (".pyc", ".pyo"):
                    raise BundleError("bytecode forbidden in immutable source closure")
                if path.is_symlink():
                    if path in aliases or path in file_aliases:
                        continue
                    raise BundleError("symlink in immutable closure")
                if (
                    role in ("server", "tool")
                    and path.is_dir()
                    and path not in known_directories
                ):
                    raise BundleError("unlisted source namespace directory")
                if path.is_file():
                    name = path.relative_to(root).as_posix()
                    if (role, name) not in paths and not (
                        role == "tool" and name == INVENTORY
                    ):
                        raise BundleError("unlisted closure input")
    return paths


def _verify_elf_context(
    context,
    seeds,
    environment,
    *,
    main,
    binaries,
    paths,
    providers,
    loader_roots,
    host_alias_targets,
    interpreters,
    direct_needed,
    origin_sensitive,
    declared_aliases,
):
    """Prove bounded glibc RPATH chains for one real process entry point.

    A loader's RUNPATH suppresses RPATH only for that lookup. Its descendants
    can still see older l_loader ancestors. Late dlopen seeds also consult the
    real main map; they are not independent executable entry points.
    """
    if main not in binaries:
        raise BundleError(f"unproven {context} ELF main map")
    max_states, max_depth, max_directories = 8192, 64, 128
    local_paths = {}

    def own_paths(key):
        if key not in local_paths:
            origin = host_alias_targets.get(paths[key], paths[key])
            local_paths[key] = tuple(
                loader_directories(origin, binaries[key], loader_roots, ())
            )
        return local_paths[key]

    def effective_rpath(key):
        return () if binaries[key].get("runpath") else own_paths(key)

    def unique(values):
        result = tuple(dict.fromkeys(values))
        if len(result) > max_directories:
            raise BundleError(f"{context} ELF loader directory bound")
        return result

    main_rpath = effective_rpath(main)
    # The process main and immutable resolved ORIGIN are fixed for this call;
    # the remaining semantic state is ELF identity + ordered inherited RPATH.
    pending = [(key, (), 0) for key in sorted(set(seeds) | {main}) if key in binaries]
    scheduled = {(key, inherited) for key, inherited, _ in pending}
    visited, order_set = set(), {tuple(environment)}

    def verify_candidate(name, directories):
        provider = providers.get(name)
        candidate = next(
            (
                directory / name
                for directory in directories
                if (directory / name).is_file()
            ),
            None,
        )
        if (
            provider is None
            or candidate is None
            or host_alias_targets.get(candidate, candidate)
            != host_alias_targets.get(paths[provider], paths[provider])
        ):
            raise BundleError(f"unproven {context} ELF NEEDED loader closure")
        actual = host_alias_targets.get(paths[provider], paths[provider])
        if actual in origin_sensitive and not _same_dso_origin(
            candidate, actual, declared_aliases
        ):
            raise BundleError("DSO alias changes loading origin")
        return provider

    while pending:
        if len(scheduled) > max_states:
            raise BundleError(f"{context} ELF loader state bound")
        key, inherited, depth = pending.pop()
        if depth > max_depth:
            raise BundleError(f"{context} ELF loader depth bound")
        visited.add(key)
        abi = binaries[key]
        own = own_paths(key)
        ancestors = unique((*effective_rpath(key), *inherited))
        directories = unique(
            (*environment, *own)
            if abi.get("runpath")
            else (*ancestors, *main_rpath, *environment)
        )
        order_set.add(directories)
        dependencies = []
        if abi["interpreter"] is not None:
            interpreter = interpreters.get(abi["interpreter"])
            if interpreter is None:
                raise BundleError(f"unproven {context} ELF interpreter closure")
            dependencies.append(interpreter)
        for name in abi["needed"]:
            if _origin_needed_suffix(name) is not None:
                if context != "source python" or (key, name) not in direct_needed:
                    raise BundleError(
                        "ORIGIN NEEDED requires private Python source context"
                    )
                dependencies.append(direct_needed[key, name])
            else:
                dependencies.append(verify_candidate(name, directories))
        for dependency in dependencies:
            state = (dependency, ancestors)
            if state not in scheduled:
                scheduled.add(state)
                pending.append((dependency, ancestors, depth + 1))

    # A supplied late module may be dlopened by a different verified caller.
    # Conservatively forbid a competing provider in ANY possible ancestor
    # RPATH directory. This envelope never supplies a missing dependency: the
    # concrete main/chain lookup above must already have closed successfully.
    possible_ancestors = unique(
        directory for key in sorted(visited) for directory in effective_rpath(key)
    )
    for key in visited:
        if binaries[key].get("runpath"):
            continue
        for name in binaries[key]["needed"]:
            if _origin_needed_suffix(name) is not None:
                continue
            for directory in possible_ancestors:
                if (directory / name).is_file():
                    verify_candidate(name, (directory,))
    # Dynamic GL/EGL lookup receives the same conservative caller envelope.
    for directory in possible_ancestors:
        order_set.add(unique((directory, *environment)))
    return visited, sorted(order_set)


@dataclass(frozen=True)
class VerifiedBundle:
    source_runtime: object
    package_runtime: object
    tool_fingerprint: str


def _resolve_bundle(config, decrypt_key_hex=""):
    """Verify before constructing accepted immutable runtime maps; never writes."""
    from .model import digest
    from .package_prepare import PackageRuntime
    from .source_prepare import SourceRuntime

    roots = {role: Path(path) for role, path in config["roots"].items()}
    inventory = _file(roots["tool"], INVENTORY)
    raw = _read_bounded(inventory, MAX_JSON)
    if (
        len(raw) > MAX_JSON
        or hashlib.sha256(raw).hexdigest() != config["inventory_sha256"]
    ):
        raise BundleError("inventory pin mismatch")
    value = strict_json(raw)
    if (
        set(value)
        != {
            "schema",
            "entries",
            "descriptor_groups",
            "capture_policy",
            "source_scopes",
            "host_files",
            "entrypoints",
            "dependency_roles",
            "loader_dirs",
            "directory_aliases",
            "dynamic_profile",
        }
        or type(value["schema"]) is not int
        or value["schema"] != 1
    ):
        raise BundleError("unsupported inventory schema")
    if not value["capture_policy"] or set(value["source_scopes"]) != {"server", "tool"}:
        raise BundleError("explicit policy/source closure required")
    if value["dependency_roles"] != ["python"]:
        raise BundleError(
            "worker import closure requires only the private Python prefix"
        )
    for key in ("entrypoints", "dependency_roles", "loader_dirs"):
        if config[key] != value[key]:
            raise BundleError("operational mapping differs from canonical identity")
    aliases = directory_aliases(roots, value["directory_aliases"])
    file_aliases = {
        Path(config["host_files"][key])
        for key, entry in value["host_files"].items()
        if "alias_of" in entry
    }
    paths = verify_inventory(
        roots,
        value["entries"],
        source_scopes=value["source_scopes"],
        aliases=aliases,
        file_aliases=file_aliases,
    )
    hashes = {(e["role"], e["path"]): e["sha256"] for e in value["entries"]}
    binaries = {}
    for entry in value["entries"]:
        if entry["kind"] == "elf":
            binaries[(entry["role"], entry["path"])] = entry["abi"]
    host_paths = {}
    host_alias_targets = {}
    if not {"unshare", "mount", "sh", "env"} <= set(value["host_files"]):
        raise BundleError("fixed namespace/unit host identity missing")
    if set(config["host_files"]) != set(value["host_files"]):
        raise BundleError("host membership mismatch")
    for identity, entry in value["host_files"].items():
        if set(entry) - {"size", "sha256", "abi", "platform", "provenance", "alias_of"}:
            raise BundleError("unknown host evidence")
        path = Path(config["host_files"][identity])
        if not path.is_absolute() or not path.is_file():
            raise BundleError("invalid host input")
        current, seen = identity, set()
        for _ in range(32):
            if current in seen:
                raise BundleError("host alias cycle")
            seen.add(current)
            node = value["host_files"][current]
            node_path = Path(config["host_files"][current])
            target = node.get("alias_of")
            if target is None:
                if node_path.is_symlink() or not node_path.is_file():
                    raise BundleError("host alias must end in regular target")
                break
            if target not in value["host_files"] or not node_path.is_symlink():
                raise BundleError("explicit host alias hop required")
            target_path = Path(config["host_files"][target])
            actual_target = node_path.readlink()
            actual_target = (
                actual_target
                if actual_target.is_absolute()
                else node_path.parent / actual_target
            )
            # absolute() normalizes no symlinks; resolving would hide an
            # undeclared intermediate hop. Each hop must match literally.
            if Path(os.path.abspath(actual_target)) != Path(
                os.path.abspath(target_path)
            ):
                raise BundleError("host alias target differs from declared hop")
            if any(
                node[field] != value["host_files"][target][field]
                for field in ("size", "sha256", "abi", "platform")
            ):
                raise BundleError("host alias byte identity mismatch")
            current = target
        else:
            raise BundleError("host alias chain bound")
        host_alias_targets[path] = Path(config["host_files"][current])
        _hash(path, entry["size"], entry["sha256"])
        actual = _binary_identity(path, elf_identity)
        if (
            entry.get("abi") != actual
            or not entry.get("provenance")
            or entry.get("platform") != "linux-x86_64"
        ):
            raise BundleError("host ABI/provenance mismatch")
        binaries[("host", identity)] = actual
        host_paths[identity] = path
        paths[("host", identity)] = path
        hashes[("host", identity)] = entry["sha256"]
    providers = {}
    binary_paths = {
        **paths,
        **{("host", key): path for key, path in host_paths.items()},
    }
    loader_roots = list(roots.values()) + [path.parent for path in host_paths.values()]
    groups = value["descriptor_groups"]
    managed_dirs = list(
        dict.fromkeys(
            paths[tuple(key)].parent
            for group in ("native_files", "x_libraries")
            for key in groups[group]
        )
    )
    display_dirs = list(
        dict.fromkeys(paths[tuple(key)].parent for key in groups["x_libraries"])
    )
    configured_dirs, host_dirs = [], []
    for role, name in config["loader_dirs"]:
        if role == "host":
            host_dirs.append(host_paths[name].parent)
            configured_dirs.append(host_paths[name].parent)
        else:
            configured_dirs.append(
                roots[role] / portable_path(name) if name else roots[role]
            )
    if configured_dirs != managed_dirs or len(host_dirs) != len(set(host_dirs)):
        raise BundleError("loader mapping differs from actual managed environment")
    for key, abi in binaries.items():
        if key[0] == "host" and value["host_files"][key[1]].get("alias_of"):
            continue
        name = abi["soname"] or binary_paths[key].name
        if name in providers:
            if binary_paths[providers[name]] == binary_paths[key]:
                continue
            raise BundleError("ambiguous ELF provider")
        providers[name] = key
    # Direct NEEDED paths never consult SONAME providers or loader directories.
    # All target bytes/ABI and aliases have already been checked above.
    python_root = roots["python"]
    python_actual = {binary_paths[key]: key for key in binaries if key[0] == "python"}
    direct_needed = {}
    needed_aliases = {**aliases, **host_alias_targets}
    origin_sensitive = {
        path
        for path, key in python_actual.items()
        if any(
            _origin_needed_suffix(name) is not None for name in binaries[key]["needed"]
        )
        or any(
            "$ORIGIN" in name or "${ORIGIN}" in name
            for name in (*binaries[key]["rpath"], *binaries[key]["runpath"])
        )
    }
    # A DSO may enter through SONAME lookup or a late import, independently of
    # any direct edge below. Prove every declared loading name up front. Only
    # the configured main executable's literal alias chain has real-executable
    # ORIGIN semantics; candidate selection above still treats DSO uses as DSOs.
    main_aliases = set()
    main_key = tuple(config["entrypoints"]["python"])
    if main_key not in binaries:
        raise BundleError("ELF executable entrypoint required")
    if main_key[0] == "host":
        main_actual = host_alias_targets.get(
            binary_paths[main_key], binary_paths[main_key]
        )
        if main_actual in python_actual:
            identity = main_key[1]
            for _ in range(32):
                node = value["host_files"][identity]
                if "alias_of" not in node:
                    break
                main_aliases.add(host_paths[identity])
                identity = node["alias_of"]
    for loading_path, actual_path in host_alias_targets.items():
        if (
            actual_path in origin_sensitive
            and loading_path not in main_aliases
            and not _same_dso_origin(loading_path, actual_path, needed_aliases)
        ):
            raise BundleError("DSO alias changes loading origin")
    for key, abi in binaries.items():
        for name in abi["needed"]:
            suffix = _origin_needed_suffix(name)
            if suffix is None:
                continue
            origin = declared_path(
                host_alias_targets.get(binary_paths[key], binary_paths[key]),
                needed_aliases,
            )
            if (
                origin not in python_actual
                or key[0] not in ("python", "host")
                or value["source_scopes"].get("python", [""]) != [""]
            ):
                raise BundleError(
                    "ORIGIN NEEDED requires complete private Python origin"
                )
            target = Path(os.path.abspath(origin.parent / suffix))
            if not target.is_relative_to(python_root):
                raise BundleError("ORIGIN NEEDED escapes private Python root")
            # Preserve kernel component semantics: alias/.. uses the alias's
            # real parent, and missing/file/.. must not vanish by normalization.
            target = origin.parent
            parts = suffix.split("/")
            for index, part in enumerate(parts):
                target = Path(os.path.abspath(target / part))
                if not target.is_relative_to(python_root):
                    raise BundleError("ORIGIN NEEDED escapes private Python root")
                loading_directory = target.parent
                target = declared_path(target, needed_aliases)
                if index == len(parts) - 1 and target.parent != loading_directory:
                    # DSO ORIGIN follows its loading name, unlike the main
                    # executable. Keep one actual-origin traversal only when
                    # the final file alias preserves that verified directory.
                    raise BundleError("DSO file alias changes loading origin")
                if not target.is_relative_to(python_root):
                    raise BundleError("ORIGIN NEEDED alias escapes private Python root")
                if index < len(parts) - 1 and not target.is_dir():
                    raise BundleError("ORIGIN NEEDED non-directory component")
            if target not in python_actual:
                raise BundleError(
                    "ORIGIN NEEDED target is not inventoried private Python ELF"
                )
            direct_needed[key, name] = python_actual[target]
    descriptor = Path(config["descriptor_path"])
    if not descriptor.is_absolute() or descriptor.is_symlink():
        raise BundleError("absolute immutable descriptor required")
    descriptor_raw = _read_bounded(descriptor, MAX_JSON)
    if (
        len(descriptor_raw) > MAX_JSON
        or hashlib.sha256(descriptor_raw).hexdigest() != config["descriptor_sha256"]
    ):
        raise BundleError("descriptor pin mismatch")
    operational = strict_json(descriptor_raw, canonical_required=False)
    groups = value["descriptor_groups"]
    if set(groups) != GROUPS or set(operational) != GROUPS | {"dynamic_profile"}:
        raise BundleError("descriptor group mismatch")
    seen_descriptor = set()
    for group in sorted(GROUPS):
        keys = [tuple(key) for key in groups[group]]
        if (
            not isinstance(groups[group], list)
            or len(keys) > MAX_FILES
            or keys != sorted(set(keys))
            or any(key in seen_descriptor for key in keys)
        ):
            raise BundleError("ambiguous descriptor membership")
        seen_descriptor.update(keys)
        if any(paths[key].is_symlink() for key in keys):
            raise BundleError("descriptor entries must be real files")
        expected = [
            {"path": str(paths[tuple(key)]), "sha256": hashes[tuple(key)]}
            for key in groups[group]
        ]
        actual = operational[group]
        if group in ("dotnet", "xvfb", "xkbcomp"):
            expected = expected[0] if len(expected) == 1 else None
        if actual != expected:
            raise BundleError("descriptor membership mismatch")
    from .bundle_dynamic import verify_dynamic

    def members(names):
        return {tuple(ref) for name in names for ref in groups[name]}

    # Python extension modules and JVM components may be loaded late rather
    # than appear in their executable's NEEDED list; keep the entire supplied
    # private Python/JRE ELF prefixes in that consumer's source environment.
    contexts = [
        (
            "source " + role,
            {key for key in binaries if key[0] == role},
            host_dirs,
            tuple(config["entrypoints"][role]),
        )
        for role in ("python", "java")
    ]
    bootstrap = [tuple(config["entrypoints"]["prlimit"])] + [
        ("host", name) for name in ("unshare", "mount", "sh", "env")
    ]
    contexts.extend(("source bootstrap", {key}, host_dirs, key) for key in bootstrap)
    contexts.extend(
        (
            (
                "managed",
                members(
                    (
                        "dotnet",
                        "app_files",
                        "native_files",
                        "x_libraries",
                        "runtime_files",
                    )
                ),
                managed_dirs,
                tuple(groups["dotnet"][0]),
            ),
            # Xvfb's xkbcomp command also uses the namespace's pinned shell.
            (
                "display",
                members(("xvfb", "x_libraries")),
                display_dirs,
                tuple(groups["xvfb"][0]),
            ),
        )
    )
    xkbcomp = tuple(groups["xkbcomp"][0])
    for value_path in (*binaries[xkbcomp]["rpath"], *binaries[xkbcomp]["runpath"]):
        if "$ORIGIN" in value_path or "${ORIGIN}" in value_path:
            raise BundleError("copied xkbcomp ORIGIN loader path unsupported")
    contexts.extend(
        ("display helper", {key}, display_dirs, key)
        for key in (xkbcomp, ("host", "sh"))
    )
    interpreters = {str(path): ("host", name) for name, path in host_paths.items()}
    covered, dynamic_orders = set(), []
    for context, seeds, environment, main in contexts:
        visited, orders = _verify_elf_context(
            context,
            seeds,
            environment,
            main=main,
            binaries=binaries,
            paths=binary_paths,
            providers=providers,
            loader_roots=loader_roots,
            host_alias_targets=host_alias_targets,
            interpreters=interpreters,
            direct_needed=direct_needed,
            origin_sensitive=origin_sensitive,
            declared_aliases=needed_aliases,
        )
        covered.update(
            host_alias_targets.get(binary_paths[key], binary_paths[key])
            for key in visited
        )
        if not context.startswith("source"):
            dynamic_orders.extend(orders)
    if any(
        host_alias_targets.get(binary_paths[key], binary_paths[key]) not in covered
        for key in binaries
    ):
        raise BundleError("ELF input has no declared consumer context")
    verify_dynamic(
        value,
        roots,
        paths,
        hashes,
        aliases,
        operational["dynamic_profile"],
        loader_orders=dynamic_orders,
        host_alias_targets=host_alias_targets,
    )
    clr_names = {Path(key[1]).name for key in groups["runtime_files"]}
    if (
        not {"libcoreclr.so", "libhostfxr.so", "System.Private.CoreLib.dll"}
        <= clr_names
    ):
        raise BundleError("actual CLR/framework descriptor closure missing")
    if (
        sum(paths[tuple(key)].stat().st_size for key in groups["app_files"])
        > 64 * 1024**2
    ):
        raise BundleError("managed app copy budget exceeded")
    app_names = [Path(key[1]).name for key in groups["app_files"]]
    if len(app_names) != len(set(app_names)):
        raise BundleError("managed staging basename collision")
    for key in groups["app_files"]:
        entry = next(
            e for e in value["entries"] if (e["role"], e["path"]) == tuple(key)
        )
        if entry["kind"] != "managed" and not (
            entry["kind"] == "data"
            and entry["path"].endswith((".deps.json", ".runtimeconfig.json"))
        ):
            raise BundleError("staged app native/ELF asset forbidden")
    # Host aliases have already passed literal-hop, byte and ABI verification.
    # Admit only loading names for real descriptor members, in the real managed
    # environment, without creating another global SONAME provider.
    native_paths = {
        paths[tuple(key)]
        for group in ("native_files", "x_libraries")
        for key in groups[group]
    }
    native_alias_names = set()
    for loading_path, actual_path in host_alias_targets.items():
        if loading_path == actual_path or actual_path not in native_paths:
            continue
        try:
            same_origin = _same_dso_origin(loading_path, actual_path, needed_aliases)
        except BundleError:
            # An unproven optional name adds no FDD closure evidence.
            continue
        if not same_origin:
            continue
        candidate = next(
            (directory / loading_path.name for directory in managed_dirs
             if (directory / loading_path.name).is_file()),
            None,
        )
        if candidate is None or host_alias_targets.get(candidate, candidate) != actual_path:
            continue
        native_alias_names.add(loading_path.name)
    _managed_closure(paths, groups, native_alias_names=native_alias_names)
    tool_map = {name: hashes[(role, name)] for role, name in paths if role == "tool"}
    tool_map[INVENTORY] = config["inventory_sha256"]
    runtime_map = {str(path): hashes[key] for key, path in paths.items()}
    runtime_map.update(
        {
            str(path): value["host_files"][key]["sha256"]
            for key, path in host_paths.items()
        }
    )

    def entrypoint(name):
        key = config["entrypoints"][name]
        if name in ("python", "java", "prlimit") and tuple(key) not in binaries:
            raise BundleError("ELF executable entrypoint required")
        return host_paths[key[1]] if key[0] == "host" else paths[tuple(key)]

    source = SourceRuntime(
        python=entrypoint("python"),
        server_root=roots["server"],
        tool_root=roots["tool"],
        dependency_roots=tuple(roots[role] for role in config["dependency_roles"]),
        java=entrypoint("java"),
        unluac=entrypoint("unluac"),
        opmap=entrypoint("opmap"),
        prlimit=entrypoint("prlimit"),
        tool_manifest=tuple(sorted(tool_map.items())),
        runtime_manifest=tuple(sorted(runtime_map.items())),
        decrypt_key_hex=decrypt_key_hex,
        environment=(("LD_LIBRARY_PATH", ":".join(map(str, host_dirs))),)
        if host_dirs
        else (),
    )
    return VerifiedBundle(
        source,
        PackageRuntime(descriptor, config["descriptor_sha256"]),
        digest(tool_map),
    )


def managed_assets(deps):
    """Select portable .NET 8 linux-x64 assets from actual deps metadata."""
    target_name = deps["runtimeTarget"]["name"]
    if "/" in target_name and target_name.rsplit("/", 1)[1] != "linux-x64":
        raise BundleError("managed target RID is not linux-x64")
    target = deps["targets"][target_name]
    runtime, native = set(), set()
    fallback = ("linux-x64", "linux", "unix-x64", "unix", "any")
    for package in target.values():
        for name, version in package.get("dependencies", {}).items():
            if name + "/" + version not in target:
                raise BundleError("managed dependency graph incomplete")
        for category, output in (("runtime", runtime), ("native", native)):
            output.update(
                Path(portable_path(name)).name for name in package.get(category, {})
            )
            candidates = package.get("runtimeTargets", {})
            for info in candidates.values():
                if not isinstance(info.get("rid"), str) or info.get(
                    "assetType"
                ) not in ("runtime", "native"):
                    raise BundleError("unknown managed RID evidence")
            rid = next(
                (
                    rid
                    for rid in fallback
                    if any(
                        info["rid"] == rid and info["assetType"] == category
                        for info in candidates.values()
                    )
                ),
                None,
            )
            if rid:
                output.update(
                    Path(portable_path(name)).name
                    for name, info in candidates.items()
                    if info["rid"] == rid and info["assetType"] == category
                )
    return runtime, native


def _managed_closure(paths, groups, *, native_alias_names=frozenset()):
    apps = [tuple(key) for key in groups["app_files"]]
    admitted_names = set()
    for cli in ("SpineViewerCLI", "PortraitSceneCLI"):
        prefix = "runtime/portrait/managed/" + cli
        required = [
            ("tool", prefix + suffix)
            for suffix in (".dll", ".deps.json", ".runtimeconfig.json")
        ]
        admitted_names.update(Path(key[1]).name for key in required)
        if any(key not in apps for key in required):
            raise BundleError("FDD entry closure missing")
        deps = strict_json(
            _read_bounded(paths[required[1]], MAX_JSON), canonical_required=False
        )
        runtime = strict_json(
            _read_bounded(paths[required[2]], MAX_JSON), canonical_required=False
        )["runtimeOptions"]
        framework = runtime.get("framework", {})
        if (
            framework.get("name") != "Microsoft.NETCore.App"
            or not framework.get("version", "").startswith("8.")
            or "includedFrameworks" in runtime
        ):
            raise BundleError("FDD .NET 8 framework required")
        if (
            runtime.get("configProperties", {}).get("System.Globalization.Invariant")
            is not True
        ):
            raise BundleError("FDD invariant globalization required")
        if any(
            name.startswith(("Microsoft.WindowsDesktop.App", "runtimepack."))
            for name in deps.get("libraries", {})
        ):
            raise BundleError("desktop/self-contained managed runtime forbidden")
        managed, native = managed_assets(deps)
        admitted_names.update(managed | native)
        names = {Path(key[1]).name for key in apps}
        native_names = {
            paths[tuple(key)].name
            for group in ("native_files", "x_libraries")
            for key in groups[group]
        }
        if not managed <= names or not native <= native_names | native_alias_names:
            raise BundleError("managed deps closure incomplete")
    if {Path(key[1]).name for key in apps} - admitted_names:
        raise BundleError("unreferenced managed application baggage")


def verify_retention(config):
    """Read-only check of the existing deployment authority's explicit pin."""
    retention = config["retention"]
    release = Path(retention["release_path"])
    authority = Path(retention["authority_path"])
    if (
        not release.is_absolute()
        or release.is_symlink()
        or release.resolve() != release
        or not release.is_dir()
    ):
        raise BundleError("immutable retained release required")
    if not re.fullmatch(
        r"[0-9a-f]{40}(?:-[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12})?",
        release.name,
    ):
        raise BundleError("invalid retained release identity")
    if not authority.is_absolute() or authority.is_symlink():
        raise BundleError("explicit regular retention authority required")
    value = strict_json(_read_bounded(authority, MAX_JSON), canonical_required=False)
    if (
        set(value) != {"schema", "pins", "rollback", "releases"}
        or type(value["schema"]) is not int
        or value["schema"] != 1
    ):
        raise BundleError("invalid retention authority schema")
    record = value["releases"].get(release.name)
    if (
        str(release) not in value["pins"]
        or not isinstance(record, dict)
        or record.get("sha") != release.name[:40]
        or record.get("branch") not in ("main", "test", "debug")
    ):
        raise BundleError("release must be registered and explicitly pinned")
    if any(
        Path(config["roots"][role]) != release / "runtime/portrait-sources" / name
        for role, name in (("server", "xl_updata_server"), ("tool", "xl_updata_tool"))
    ):
        raise BundleError(
            "dedicated source roots must use fixed retained release layout"
        )


def verify_python_metadata(root, python_version, site_roots=None):
    from .bundle_python import verify_python_metadata as verify

    return verify(root, python_version, site_roots)


def verify_python_process(config, verified, inventory):
    """Bind the actual private base prefix, venv and every loaded file byte."""
    roots = {key: Path(value) for key, value in config["roots"].items()}
    base = roots["python"]
    aliases = directory_aliases(roots, inventory["directory_aliases"])
    aliases.update(
        {
            Path(config["host_files"][key]): Path(
                config["host_files"][entry["alias_of"]]
            )
            for key, entry in inventory["host_files"].items()
            if "alias_of" in entry
        }
    )
    if (
        tuple(sys.version_info[:2]) not in ((3, 11), (3, 12))
        or Path(sys.base_prefix).resolve() != base.resolve()
    ):
        raise BundleError("complete private Python 3.11/3.12 base prefix required")
    prefix = declared_path(Path(sys.prefix), aliases)
    if (
        not prefix.is_relative_to(base)
        or Path(sys.executable) != verified.source_runtime.python
    ):
        raise BundleError("Python executable/venv differs from verified configuration")
    import site

    from .bundle_python import verify_python_frontier

    manifest = dict(verified.source_runtime.runtime_manifest)
    if Path(sys.prefix) == Path(sys.base_prefix) or site.ENABLE_USER_SITE is not False:
        raise BundleError("private venv with disabled user site required")
    venv_config = Path(sys.prefix) / "pyvenv.cfg"
    settings = {}
    for line in _read_bounded(venv_config, 65536).decode("utf-8").splitlines():
        if not line.strip() or line.lstrip().startswith("#"):
            continue
        key, separator, value = line.partition("=")
        if not separator or key.strip().lower() in settings:
            raise BundleError("invalid venv configuration")
        settings[key.strip().lower()] = value.strip()
    if settings.get("include-system-site-packages", "").lower() != "false":
        raise BundleError("venv system site packages forbidden")
    verify_python_metadata(
        base,
        tuple(sys.version_info[:3]),
        [
            Path(path)
            for path in sys.path
            if Path(path).is_dir() and Path(path).is_relative_to(base)
        ],
    )
    archives = verify_python_frontier(base, sys.path, aliases, manifest)
    required = [Path(sys.executable)]
    if Path(sys.prefix) != Path(sys.base_prefix):
        required.append(Path(sys.prefix) / "pyvenv.cfg")
    for module in tuple(sys.modules.values()):
        filename = getattr(module, "__file__", None)
        if filename:
            required.append(Path(filename))
    for path in required:
        actual = declared_path(path, aliases)
        expected = manifest.get(str(actual))
        if expected is None or not actual.is_file():
            container = next(
                (parent for parent in actual.parents if parent in archives), None
            )
            if (
                container is None
                or actual.relative_to(container).as_posix() not in archives[container]
            ):
                raise BundleError("loaded Python file outside actual verified closure")
        else:
            _hash(actual, actual.stat().st_size, expected)
    for value in sys.path:
        if not value:
            raise BundleError("ambient Python current-directory import forbidden")
        path = declared_path(Path(value), aliases)
        if not path.is_relative_to(base) and path not in (
            roots["server"],
            roots["tool"],
        ):
            raise BundleError("ambient Python import directory outside verified roots")


SOURCE_DIRECTORIES = {
    "server": ("server_app/portraits",),
    "tool": ("app/features/preview", "app/platform", "app/features/characters/parser"),
}
SOURCE_FILES = {
    "server": ("server_app/__init__.py", "server_app/lua_decoder.py"),
    "tool": (
        "app/__init__.py",
        "app/features/__init__.py",
        "app/features/characters/__init__.py",
        "app/shared/__init__.py",
        "app/shared/contracts.py",
        "app/shared/lua.py",
    ),
}


def verify_source_recipe(config, verified):
    """Current same-revision worker source closure, including later lazy imports."""
    manifest = dict(verified.source_runtime.runtime_manifest)
    for role, directories in SOURCE_DIRECTORIES.items():
        root = Path(config["roots"][role])
        required = [_file(root, name) for name in SOURCE_FILES[role]]
        known_directories = {
            parent
            for name in manifest
            for parent in Path(name).parents
            if parent.is_relative_to(root)
        }
        for index, path in enumerate(root.rglob("*"), 1):
            if (
                index > MAX_FILES
                or path.is_symlink()
                or path.suffix in (".pyc", ".pyo")
            ):
                raise BundleError("invalid complete source closure")
            if path.is_dir() and path not in known_directories:
                raise BundleError("unlisted source namespace directory")
            if path.is_file() and not (
                role == "tool" and path.relative_to(root).as_posix() == INVENTORY
            ):
                required.append(path)
        for directory in directories:
            base = root / directory
            if not base.is_dir() or base.is_symlink():
                raise BundleError("required worker source namespace missing")
            for index, path in enumerate(base.rglob("*"), 1):
                if (
                    index > MAX_FILES
                    or path.is_symlink()
                    or path.suffix in (".pyc", ".pyo")
                ):
                    raise BundleError("invalid worker source closure")
                if path.is_file():
                    required.append(path)
        for path in required:
            if str(path) not in manifest:
                raise BundleError("worker source omitted from canonical identity")
            _hash(path, path.stat().st_size, manifest[str(path)])


def _running_imports(config, verified):
    roots = {name: Path(config["roots"][name]).resolve() for name in ("server", "tool")}
    admitted = dict(verified.source_runtime.runtime_manifest)
    for name, module in tuple(sys.modules.items()):
        role = (
            "server"
            if name == "server_app" or name.startswith("server_app.")
            else "tool"
            if name == "app" or name.startswith("app.")
            else None
        )
        if role is None:
            continue
        filename = getattr(module, "__file__", None)
        if not filename:
            raise BundleError("unidentified imported runtime module")
        path = Path(filename)
        if not path.resolve().is_relative_to(roots[role]) or str(path) not in admitted:
            raise BundleError("running source revision differs from verified bundle")
    expected = [str(roots["server"]), str(roots["tool"])]
    actual = [str(Path(item).resolve()) for item in sys.path if item]
    if actual[:2] != expected:
        raise BundleError("explicit immutable server/tool import paths required")
    for identity in ("unshare", "mount", "sh", "env"):
        if config["host_files"].get(identity) != "/usr/bin/" + identity:
            raise BundleError("fixed host operational path mismatch")


def resolve_bundle(config, decrypt_key_hex=""):
    """Production resolver binds loaded code as well as supplied immutable bytes."""
    verified = _resolve_bundle(config, decrypt_key_hex)
    verify_source_recipe(config, verified)
    _running_imports(config, verified)
    inventory = strict_json(
        _read_bounded(Path(config["roots"]["tool"]) / INVENTORY, MAX_JSON)
    )
    verify_python_process(config, verified, inventory)
    verify_retention(config)
    return verified


def preflight(config):
    """Configuration evidence only; admission additionally checks live guards/caps."""
    verified = resolve_bundle(config)
    from . import reader  # pure Reader import, no construction or store writes

    del reader
    return {
        "enabled": False,
        "configuration_verified": True,
        "execution_admitted": False,
        "tool_fingerprint": verified.tool_fingerprint,
    }


def main(argv=None):
    import argparse

    parser = argparse.ArgumentParser(
        description="Read-only portrait bundle verification"
    )
    parser.add_argument("--config", required=True, help="Explicit trusted bundle JSON")
    args = parser.parse_args(argv)
    try:
        config = strict_json(
            _read_bounded(Path(args.config), MAX_JSON), canonical_required=False
        )
        print(json.dumps(preflight(config)))
        return 0
    except (ValueError, OSError, KeyError, TypeError):
        # Do not echo configuration or a secret environment value in diagnostics.
        print(
            json.dumps(
                {
                    "enabled": False,
                    "configuration_verified": False,
                    "execution_admitted": False,
                    "error": "bundle_unready",
                }
            )
        )
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
