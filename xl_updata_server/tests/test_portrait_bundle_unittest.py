"""Focused stdlib bundle identity tests; no renderer or subprocess."""

import hashlib
import importlib.util
import json
import os
import struct
import tempfile
import unittest
from contextlib import ExitStack
from pathlib import Path
from unittest.mock import patch


def canonical(value):
    return json.dumps(
        value, sort_keys=True, separators=(",", ":"), allow_nan=False
    ).encode()


def elf(*, needed=(), soname=None, runpath=None):
    strings = bytearray(b"\0")
    tags = []
    for tag, values in (
        (1, needed),
        (14, [soname] if soname else []),
        (29, [runpath] if runpath else []),
    ):
        for value in values:
            tags.append((tag, len(strings)))
            strings.extend(value.encode() + b"\0")
    tags.append((0, 0))
    dynamic = b"".join(struct.pack("<qQ", *row) for row in tags)
    header = (
        b"\x7fELF\x02\x01\x01"
        + bytes(9)
        + struct.pack("<HHIQQQIHHHHHH", 3, 62, 1, 0, 0, 64, 0, 64, 0, 0, 64, 3, 0)
    )
    sections = bytes(64) + struct.pack(
        "<IIQQQQIIQQ", 0, 3, 0, 0, 256, len(strings), 0, 0, 1, 0
    )
    sections += struct.pack(
        "<IIQQQQIIQQ", 0, 6, 0, 0, 256 + len(strings), len(dynamic), 1, 0, 8, 16
    )
    return header + sections + strings + dynamic


def managed_pe():
    data = bytearray(512)
    data[:2] = b"MZ"
    struct.pack_into("<I", data, 60, 64)
    data[64:68] = b"PE\0\0"
    struct.pack_into("<HHIIIHH", data, 68, 0x14C, 1, 0, 0, 0, 224, 0)
    struct.pack_into("<H", data, 88, 0x10B)
    struct.pack_into("<II", data, 88 + 96 + 14 * 8, 4096, 72)
    struct.pack_into("<IIII", data, 88 + 224 + 8, 128, 4096, 128, 384)
    struct.pack_into("<II", data, 384 + 8, 4176, 32)
    struct.pack_into("<I", data, 384 + 16, 1)
    data[464:468] = b"BSJB"
    return bytes(data)


def fixture(root):
    from server_app.portraits.bundle import elf_identity

    roles = ("server", "tool", "python", "dotnet", "java", "native", "display")
    roots = {role: root / role for role in roles}
    for path in roots.values():
        path.mkdir(parents=True)
    entries = []

    def add(role, name, data, kind="data"):
        path = roots[role] / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)
        entry = {
            "role": role,
            "path": name,
            "size": len(data),
            "sha256": hashlib.sha256(data).hexdigest(),
            "kind": kind,
        }
        if kind == "elf":
            entry.update(
                platform="linux-x86_64",
                provenance="fixture synthetic ELF, not deployment evidence",
                abi=elf_identity(data),
            )
        if kind == "managed":
            entry.update(platform="net8.0", provenance="synthetic PE fixture")
        entries.append(entry)
        return [role, name]

    add("server", "server_app/worker.py", b"pass\n", "source")
    apps = []
    for cli in ("SpineViewerCLI", "PortraitSceneCLI"):
        prefix = "runtime/portrait/managed/" + cli
        apps.append(add("tool", prefix + ".dll", managed_pe(), "managed"))
        apps.append(
            add(
                "tool",
                prefix + ".deps.json",
                canonical(
                    {
                        "runtimeTarget": {"name": "net8.0"},
                        "targets": {
                            "net8.0": {"fixture/1": {"runtime": {cli + ".dll": {}}}}
                        },
                    }
                ),
            )
        )
        apps.append(
            add(
                "tool",
                prefix + ".runtimeconfig.json",
                canonical(
                    {
                        "runtimeOptions": {
                            "configProperties": {
                                "System.Globalization.Invariant": True
                            },
                            "framework": {
                                "name": "Microsoft.NETCore.App",
                                "version": "8.0.0",
                            },
                        }
                    }
                ),
            )
        )
    clr = [
        add("dotnet", name, elf(), "elf") for name in ("libcoreclr.so", "libhostfxr.so")
    ]
    clr.append(add("dotnet", "System.Private.CoreLib.dll", managed_pe(), "managed"))
    python = add("python", "python", elf(), "elf")
    java = add("java", "java", elf(), "elf")
    dotnet = add("dotnet", "dotnet", elf(), "elf")
    xvfb = add(
        "display", "Xvfb", elf() + b"/usr/share/X11/xkb\0/usr/bin/xkbcomp\0", "elf"
    )
    xkbcomp = add("display", "xkbcomp", elf(), "elf")
    unluac = add("tool", "tools/unluac.jar", b"fixture jar")
    opmap = add("tool", "tools/opmap", b"fixture map")
    prlimit = add("native", "prlimit", elf(), "elf")
    dynamic_profile = {
        "schema": "mesa-llvmpipe-xvfb-invariant/v1",
        "font_mode": "directory",
        "dri_driver": add("native", "dri/swrast_dri.so", elf(), "elf"),
        "glx_vendor": add("native", "libGLX_mesa.so.0", elf(), "elf"),
        "egl_vendor": add("native", "libEGL_mesa.so.0", elf(), "elf"),
        "egl_vendor_json": add(
            "display",
            "egl-vendor.json",
            canonical(
                {
                    "file_format_version": "1.0.0",
                    "ICD": {"library_path": "libEGL_mesa.so.0"},
                }
            ),
        ),
        "share_tree": ["display", "share"],
        "font_tree": ["display", "share/fonts/X11/misc"],
    }
    for name in (
        "X11/xkb/rules/base",
        "X11/xkb/keycodes/evdev",
        "X11/xkb/symbols/pc",
        "X11/xkb/types/complete",
        "X11/xkb/compat/complete",
        "X11/xkb/geometry/pc",
        "X11/locale/locale.alias",
        "fonts/X11/misc/fixed.pcf",
    ):
        add("display", "share/" + name, b"synthetic resource, not Native evidence")
    add("display", "share/fonts/X11/misc/fonts.dir", b"1\nfixed.pcf fixed\n")
    dynamic_profile["tree_manifest"] = add(
        "display",
        "tree-manifest.json",
        canonical(
            {
                "schema": 1,
                "aliases": [],
                "files": sorted(
                    [
                        {"path": e["path"][len("share/") :], "sha256": e["sha256"]}
                        for e in entries
                        if e["role"] == "display" and e["path"].startswith("share/")
                    ],
                    key=lambda row: row["path"],
                ),
            }
        ),
    )
    groups = {
        "dotnet": [dotnet],
        "app_files": sorted(apps),
        "native_files": [],
        "xvfb": [xvfb],
        "xkbcomp": [xkbcomp],
        "x_libraries": sorted(
            dynamic_profile[key] for key in ("dri_driver", "egl_vendor", "glx_vendor")
        ),
        "runtime_files": sorted(clr),
    }
    value = {
        "schema": 1,
        "dynamic_profile": dynamic_profile,
        "directory_aliases": [],
        "entries": sorted(entries, key=lambda e: (e["role"], e["path"])),
        "descriptor_groups": groups,
        "capture_policy": {"schema": 1, "width": 32},
        "source_scopes": {
            "server": ["server_app"],
            "tool": ["runtime/portrait/managed", "tools"],
        },
        "host_files": {},
    }
    raw = canonical(value)
    (roots["tool"] / "runtime/portrait/inventory.json").write_bytes(raw)
    config = {
        "roots": {k: str(v) for k, v in roots.items()},
        "inventory_sha256": hashlib.sha256(raw).hexdigest(),
        "descriptor_path": str(root / "descriptor.json"),
        "host_files": {},
        "entrypoints": {
            "python": python,
            "java": java,
            "unluac": unluac,
            "opmap": opmap,
            "prlimit": prlimit,
        },
        "dependency_roles": ["python"],
        "loader_dirs": [["native", "dri"], ["native", ""]],
    }
    value.update(
        {key: config[key] for key in ("entrypoints", "dependency_roles", "loader_dirs")}
    )
    raw = canonical(value)
    Path(config["roots"]["tool"], "runtime/portrait/inventory.json").write_bytes(raw)
    config["inventory_sha256"] = hashlib.sha256(raw).hexdigest()
    host = root / "host"
    host.mkdir(exist_ok=True)
    for name in ("unshare", "mount", "sh", "env"):
        data = elf()
        path = host / name
        path.write_bytes(data)
        value["host_files"][name] = {
            "size": len(data),
            "sha256": hashlib.sha256(data).hexdigest(),
            "abi": elf_identity(data),
            "platform": "linux-x86_64",
            "provenance": "synthetic fixed host fixture",
        }
        config["host_files"][name] = str(path)
    raw = canonical(value)
    Path(config["roots"]["tool"], "runtime/portrait/inventory.json").write_bytes(raw)
    config["inventory_sha256"] = hashlib.sha256(raw).hexdigest()
    write_descriptor(config, value)
    return config, value


def write_descriptor(config, value):
    entries = {(e["role"], e["path"]): e for e in value["entries"]}
    descriptor = {}
    for group, keys in value["descriptor_groups"].items():
        rows = [
            {
                "path": config["host_files"][name]
                if role == "host"
                else str(Path(config["roots"][role]) / name),
                "sha256": value["host_files"][name]["sha256"]
                if role == "host"
                else entries[(role, name)]["sha256"],
            }
            for role, name in keys
        ]
        descriptor[group] = rows[0] if group in ("dotnet", "xvfb", "xkbcomp") else rows
    if "dynamic_profile" in value:
        from server_app.portraits.bundle_dynamic import operational_profile

        roots = {key: Path(path) for key, path in config["roots"].items()}
        paths = {key: roots[key[0]] / key[1] for key in entries}
        hashes = {key: entry["sha256"] for key, entry in entries.items()}
        descriptor["dynamic_profile"] = operational_profile(
            value, roots, paths, hashes, {}
        )
    raw = canonical(descriptor)
    Path(config["descriptor_path"]).write_bytes(raw)
    config["descriptor_sha256"] = hashlib.sha256(raw).hexdigest()


class BundleTests(unittest.TestCase):
    def test_relocation_and_runtime_actual_bytes(self):
        from server_app.portraits.bundle import BundleError
        from server_app.portraits.bundle import _resolve_bundle as resolve_bundle

        with tempfile.TemporaryDirectory() as temp:
            base = Path(temp)
            config, _value = fixture(base / "a")
            first = resolve_bundle(config)
            config2, _value2 = fixture(base / "b")
            second = resolve_bundle(config2)
            self.assertEqual(first.tool_fingerprint, second.tool_fingerprint)
            self.assertNotEqual(
                first.package_runtime.manifest_sha256,
                second.package_runtime.manifest_sha256,
            )
            self.assertIn(
                str(Path(config["roots"]["server"]) / "server_app/worker.py"),
                dict(first.source_runtime.runtime_manifest),
            )
            Path(config["roots"]["java"], "java").write_bytes(b"mutation")
            with self.assertRaises(BundleError):
                resolve_bundle(config)

    def test_descriptor_extra_and_fdd_missing_fail(self):
        from server_app.portraits.bundle import BundleError
        from server_app.portraits.bundle import _resolve_bundle as resolve_bundle

        with tempfile.TemporaryDirectory() as temp:
            config, _value = fixture(Path(temp))
            path = Path(config["descriptor_path"])
            descriptor = json.loads(path.read_bytes())
            descriptor["runtime_files"].append(descriptor["dotnet"])
            raw = canonical(descriptor)
            path.write_bytes(raw)
            config["descriptor_sha256"] = hashlib.sha256(raw).hexdigest()
            with self.assertRaises(BundleError):
                resolve_bundle(config)

    def test_safe_origin_runpath_and_unknown_variables(self):
        from server_app.portraits.bundle import (
            BundleError,
            elf_identity,
            loader_directories,
        )

        abi = elf_identity(elf(needed=["libx.so"], runpath="$ORIGIN/../lib"))
        self.assertEqual(abi["runpath"], ["$ORIGIN/../lib"])
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            (root / "lib").mkdir()
            (root / "bin").mkdir()
            self.assertEqual(
                loader_directories(root / "bin/app", abi, [root], []), [root / "lib"]
            )
            for unsafe in ("", "$HOME/lib", "/outside", "."):
                with self.assertRaises(BundleError):
                    loader_directories(
                        root / "bin/app", {"runpath": [unsafe], "rpath": []}, [root], []
                    )

    def test_bytecode_rejected(self):
        from server_app.portraits.bundle import BundleError
        from server_app.portraits.bundle import _resolve_bundle as resolve_bundle

        with tempfile.TemporaryDirectory() as temp:
            config, _value = fixture(Path(temp))
            cache = Path(config["roots"]["server"]) / "server_app/__pycache__"
            cache.mkdir()
            (cache / "worker.cpython-312.pyc").write_bytes(b"stale")
            with self.assertRaises(BundleError):
                resolve_bundle(config)

    def test_policy_mapping_cannot_change_without_inventory(self):
        from server_app.portraits.bundle import BundleError
        from server_app.portraits.bundle import _resolve_bundle as resolve_bundle

        with tempfile.TemporaryDirectory() as temp:
            config, _value = fixture(Path(temp))
            config["dependency_roles"] = ["native"]
            with self.assertRaises(BundleError):
                resolve_bundle(config)

    def test_needed_requires_actual_loader_provider(self):
        from server_app.portraits.bundle import (
            BundleError,
            elf_identity,
        )
        from server_app.portraits.bundle import (
            _resolve_bundle as resolve_bundle,
        )

        with tempfile.TemporaryDirectory() as temp:
            config, value = fixture(Path(temp))
            data = elf(needed=["libmissing.so"], runpath="$ORIGIN")
            Path(config["roots"]["java"], "java").write_bytes(data)
            entry = next(e for e in value["entries"] if e["role"] == "java")
            entry.update(
                size=len(data),
                sha256=hashlib.sha256(data).hexdigest(),
                abi=elf_identity(data),
            )
            raw = canonical(value)
            Path(
                config["roots"]["tool"], "runtime/portrait/inventory.json"
            ).write_bytes(raw)
            config["inventory_sha256"] = hashlib.sha256(raw).hexdigest()
            with self.assertRaises(BundleError):
                resolve_bundle(config)

    def test_native_pe_cannot_claim_managed(self):
        from server_app.portraits.bundle import BundleError, managed_identity

        for data in (b"fixture managed bytes", b"MZ" + bytes(256)):
            with self.assertRaises(BundleError):
                managed_identity(data)

    def test_elf_interpreter_is_parsed(self):
        from server_app.portraits.bundle import elf_identity

        data = bytearray(elf())
        interp = b"/lib64/ld-linux-x86-64.so.2\0"
        phoff = len(data)
        struct.pack_into("<Q", data, 32, phoff)
        struct.pack_into("<HH", data, 54, 56, 1)
        data.extend(
            struct.pack(
                "<IIQQQQQQ", 3, 0, phoff + 56, 0, 0, len(interp), len(interp), 1
            )
        )
        data.extend(interp)
        self.assertEqual(elf_identity(data)["interpreter"], interp[:-1].decode())

    def test_managed_dependencies_missing_fail_closed(self):
        from server_app.portraits.bundle import BundleError
        from server_app.portraits.bundle import _resolve_bundle as resolve_bundle

        with tempfile.TemporaryDirectory() as temp:
            config, value = fixture(Path(temp))
            entry = next(
                e
                for e in value["entries"]
                if e["path"].endswith("SpineViewerCLI.deps.json")
            )
            path = Path(config["roots"][entry["role"]]) / entry["path"]
            deps = json.loads(path.read_bytes())
            deps["targets"]["net8.0"]["fixture/1"]["runtime"]["missing.dll"] = {}
            raw = canonical(deps)
            path.write_bytes(raw)
            entry.update(size=len(raw), sha256=hashlib.sha256(raw).hexdigest())
            raw = canonical(value)
            Path(
                config["roots"]["tool"], "runtime/portrait/inventory.json"
            ).write_bytes(raw)
            config["inventory_sha256"] = hashlib.sha256(raw).hexdigest()
            write_descriptor(config, value)
            with self.assertRaises(BundleError):
                resolve_bundle(config)

    def test_entrypoint_must_be_verified_executable(self):
        from server_app.portraits.bundle import BundleError
        from server_app.portraits.bundle import _resolve_bundle as resolve_bundle

        with tempfile.TemporaryDirectory() as temp:
            config, value = fixture(Path(temp))
            config["entrypoints"]["python"] = ["tool", "tools/opmap"]
            value["entrypoints"] = config["entrypoints"]
            raw = canonical(value)
            Path(
                config["roots"]["tool"], "runtime/portrait/inventory.json"
            ).write_bytes(raw)
            config["inventory_sha256"] = hashlib.sha256(raw).hexdigest()
            with self.assertRaises(BundleError):
                resolve_bundle(config)

    def test_preflight_only_reports_configuration(self):
        from server_app.portraits.bundle import preflight

        with tempfile.TemporaryDirectory() as temp:
            config, _value = fixture(Path(temp))
            with (
                patch("server_app.portraits.bundle._running_imports"),
                patch("server_app.portraits.bundle.verify_retention"),
                patch("server_app.portraits.bundle.verify_python_process"),
                patch("server_app.portraits.bundle.verify_source_recipe"),
            ):
                result = preflight(config)
            self.assertTrue(result["configuration_verified"])
            self.assertFalse(result["execution_admitted"])
            self.assertFalse(result["enabled"])

    def test_source_scope_symlink_and_empty_scope_fail_closed(self):
        from server_app.portraits.bundle import BundleError
        from server_app.portraits.bundle import _resolve_bundle as resolve_bundle

        with tempfile.TemporaryDirectory() as temp:
            config, value = fixture(Path(temp))
            value["source_scopes"]["server"] = []
            raw = canonical(value)
            Path(
                config["roots"]["tool"], "runtime/portrait/inventory.json"
            ).write_bytes(raw)
            config["inventory_sha256"] = hashlib.sha256(raw).hexdigest()
            with self.assertRaises(BundleError):
                resolve_bundle(config)

    def test_runtime_descriptor_requires_actual_clr_framework(self):
        from server_app.portraits.bundle import BundleError
        from server_app.portraits.bundle import _resolve_bundle as resolve_bundle

        with tempfile.TemporaryDirectory() as temp:
            config, value = fixture(Path(temp))
            value["descriptor_groups"]["runtime_files"] = []
            raw = canonical(value)
            Path(
                config["roots"]["tool"], "runtime/portrait/inventory.json"
            ).write_bytes(raw)
            config["inventory_sha256"] = hashlib.sha256(raw).hexdigest()
            write_descriptor(config, value)
            with self.assertRaises(BundleError):
                resolve_bundle(config)

    def test_loader_mapping_matches_actual_descriptor_environment(self):
        from server_app.portraits.bundle import BundleError
        from server_app.portraits.bundle import _resolve_bundle as resolve_bundle

        with tempfile.TemporaryDirectory() as temp:
            config, value = fixture(Path(temp))
            value["descriptor_groups"]["native_files"] = [["native", "prlimit"]]
            raw = canonical(value)
            Path(
                config["roots"]["tool"], "runtime/portrait/inventory.json"
            ).write_bytes(raw)
            config["inventory_sha256"] = hashlib.sha256(raw).hexdigest()
            write_descriptor(config, value)
            with self.assertRaises(BundleError):
                resolve_bundle(config)

    def test_host_library_is_bound_to_actual_descriptor_and_source_environment(self):
        from server_app.portraits.bundle import _resolve_bundle as resolve_bundle
        from server_app.portraits.bundle import elf_identity

        with tempfile.TemporaryDirectory() as temp:
            config, value = fixture(Path(temp))
            host = Path(temp) / "host"
            host.mkdir(exist_ok=True)
            library = host / "libfixture.so"
            data = elf(soname="libfixture.so")
            library.write_bytes(data)
            value["host_files"]["fixture"] = {
                "size": len(data),
                "sha256": hashlib.sha256(data).hexdigest(),
                "abi": elf_identity(data),
                "platform": "linux-x86_64",
                "provenance": "synthetic fixture",
            }
            config["host_files"]["fixture"] = str(library)
            value["descriptor_groups"]["x_libraries"].insert(0, ["host", "fixture"])
            config["loader_dirs"] = value["loader_dirs"] = [
                ["host", "fixture"],
                ["native", "dri"],
                ["native", ""],
            ]
            data = elf(needed=["libfixture.so"])
            entry = next(e for e in value["entries"] if e["role"] == "java")
            Path(config["roots"]["java"], "java").write_bytes(data)
            entry.update(
                size=len(data),
                sha256=hashlib.sha256(data).hexdigest(),
                abi=elf_identity(data),
            )
            raw = canonical(value)
            Path(
                config["roots"]["tool"], "runtime/portrait/inventory.json"
            ).write_bytes(raw)
            config["inventory_sha256"] = hashlib.sha256(raw).hexdigest()
            write_descriptor(config, value)
            bundle = resolve_bundle(config)
            self.assertEqual(
                dict(bundle.source_runtime.environment)["LD_LIBRARY_PATH"], str(host)
            )
            self.assertIn(str(library), dict(bundle.source_runtime.runtime_manifest))

    def test_explicit_host_symlink_alias_is_verified(self):
        from server_app.portraits.bundle import BundleError, elf_identity
        from server_app.portraits.bundle import _resolve_bundle as resolve_bundle

        with tempfile.TemporaryDirectory() as temp:
            config, value = fixture(Path(temp))
            host = Path(temp) / "host"
            host.mkdir(exist_ok=True)
            target, alias = host / "dash", host / "sh"
            data = elf()
            target.write_bytes(data)
            stack = self.enterContext(ExitStack())
            if os.name == "nt":
                # Windows account lacks symlink privilege even controlled. Test
                # graph/readlink semantics with real hashed bytes; POSIX uses a
                # real symlink. This does not claim Windows OS alias evidence.
                alias.write_bytes(data)
                original_is_link, original_readlink = Path.is_symlink, Path.readlink
                stack.enter_context(
                    patch.object(
                        Path, "is_symlink", lambda p: p == alias or original_is_link(p)
                    )
                )
                stack.enter_context(
                    patch.object(
                        Path,
                        "readlink",
                        lambda p: target if p == alias else original_readlink(p),
                    )
                )
            else:
                alias.unlink()  # this test owns this single fixture file
                alias.symlink_to(target)
            entry = {
                "size": len(data),
                "sha256": hashlib.sha256(data).hexdigest(),
                "abi": elf_identity(data),
                "platform": "linux-x86_64",
                "provenance": "fixture",
            }
            value["host_files"].update(
                {"dash": entry, "sh": {**entry, "alias_of": "dash"}}
            )
            config["host_files"].update({"dash": str(target), "sh": str(alias)})
            raw = canonical(value)
            Path(
                config["roots"]["tool"], "runtime/portrait/inventory.json"
            ).write_bytes(raw)
            config["inventory_sha256"] = hashlib.sha256(raw).hexdigest()
            bundle = resolve_bundle(config)
            self.assertIn(str(alias), dict(bundle.source_runtime.runtime_manifest))
            value["host_files"]["sh"]["alias_of"] = "sh"
            raw = canonical(value)
            Path(
                config["roots"]["tool"], "runtime/portrait/inventory.json"
            ).write_bytes(raw)
            config["inventory_sha256"] = hashlib.sha256(raw).hexdigest()
            with self.assertRaises(BundleError):
                resolve_bundle(config)

    def test_public_resolver_rejects_other_running_revision(self):
        from server_app.portraits.bundle import BundleError, resolve_bundle

        with tempfile.TemporaryDirectory() as temp:
            config, _ = fixture(Path(temp))
            with self.assertRaises(BundleError):
                resolve_bundle(config)

    def test_unknown_entry_fields_fail_closed(self):
        from server_app.portraits.bundle import BundleError, _resolve_bundle

        with tempfile.TemporaryDirectory() as temp:
            config, value = fixture(Path(temp))
            value["entries"][0]["unsupported_evidence"] = True
            raw = canonical(value)
            Path(
                config["roots"]["tool"], "runtime/portrait/inventory.json"
            ).write_bytes(raw)
            config["inventory_sha256"] = hashlib.sha256(raw).hexdigest()
            with self.assertRaises(BundleError):
                _resolve_bundle(config)

    def test_namespace_and_unit_host_inputs_cannot_be_omitted(self):
        from server_app.portraits.bundle import BundleError, _resolve_bundle

        with tempfile.TemporaryDirectory() as temp:
            config, value = fixture(Path(temp))
            config["host_files"] = value["host_files"] = {}
            raw = canonical(value)
            Path(
                config["roots"]["tool"], "runtime/portrait/inventory.json"
            ).write_bytes(raw)
            config["inventory_sha256"] = hashlib.sha256(raw).hexdigest()
            with self.assertRaises(BundleError):
                _resolve_bundle(config)

    def test_retention_requires_registered_immutable_pin_readonly(self):
        from server_app.portraits.bundle import BundleError, verify_retention

        with tempfile.TemporaryDirectory() as temp:
            base = Path(temp)
            release = base / ("a" * 40)
            release.mkdir()
            authority = base / "release-storage.json"
            value = {
                "schema": 1,
                "pins": [str(release)],
                "rollback": [],
                "releases": {
                    release.name: {"branch": "test", "sha": "a" * 40, "created_at": 1}
                },
            }
            raw = canonical(value)
            authority.write_bytes(raw)
            config = {
                "retention": {
                    "authority_path": str(authority),
                    "release_path": str(release),
                },
                "roots": {
                    "server": str(
                        release / "runtime/portrait-sources/xl_updata_server"
                    ),
                    "tool": str(release / "runtime/portrait-sources/xl_updata_tool"),
                },
            }
            verify_retention(config)
            self.assertEqual(authority.read_bytes(), raw)
            value["pins"] = []
            authority.write_bytes(canonical(value))
            with self.assertRaises(BundleError):
                verify_retention(config)

    def test_linux_net8_readytorun_metadata_supported(self):
        from server_app.portraits.bundle import BundleError, managed_identity

        data = bytearray(managed_pe())
        data[328:368] = data[312:352]
        struct.pack_into("<H", data, 68, 0xFD1D)
        struct.pack_into("<H", data, 84, 240)
        struct.pack_into("<H", data, 88, 0x20B)
        struct.pack_into("<II", data, 88 + 112 + 14 * 8, 4096, 72)
        struct.pack_into("<I", data, 400, 0x0C)
        struct.pack_into("<II", data, 448, 4192, 16)
        data[480:484] = b"RTR\0"
        struct.pack_into("<HH", data, 484, 9, 1)
        self.assertEqual(managed_identity(data)["format"], "CLR-R2R-linux-x86_64")
        data[480:484] = b"BAD!"
        with self.assertRaises(BundleError):
            managed_identity(data)

    def test_fdd_assets_follow_linux_rid_metadata(self):
        from server_app.portraits.bundle import BundleError, managed_assets

        deps = {
            "runtimeTarget": {"name": ".NETCoreApp,Version=v8.0"},
            "targets": {
                ".NETCoreApp,Version=v8.0": {
                    "package/1": {
                        "runtime": {"lib/net8.0/a.dll": {}},
                        "runtimeTargets": {
                            "runtimes/win-x64/native/a.dll": {
                                "rid": "win-x64",
                                "assetType": "native",
                            },
                            "runtimes/linux-x64/native/liba.so": {
                                "rid": "linux-x64",
                                "assetType": "native",
                            },
                        },
                    }
                }
            },
        }
        self.assertEqual(managed_assets(deps), ({"a.dll"}, {"liba.so"}))
        deps["targets"][".NETCoreApp,Version=v8.0"]["package/1"]["dependencies"] = {
            "missing": "1"
        }
        with self.assertRaises(BundleError):
            managed_assets(deps)

    def test_unreferenced_managed_baggage_is_not_admitted(self):
        from server_app.portraits.bundle import BundleError, _resolve_bundle

        with tempfile.TemporaryDirectory() as temp:
            config, value = fixture(Path(temp))
            path = "runtime/portrait/managed/unused.dll"
            data = managed_pe()
            Path(config["roots"]["tool"], path).write_bytes(data)
            value["entries"].append(
                {
                    "role": "tool",
                    "path": path,
                    "size": len(data),
                    "sha256": hashlib.sha256(data).hexdigest(),
                    "kind": "managed",
                    "platform": "net8.0",
                    "provenance": "fixture",
                }
            )
            value["entries"].sort(key=lambda e: (e["role"], e["path"]))
            value["descriptor_groups"]["app_files"].append(["tool", path])
            value["descriptor_groups"]["app_files"].sort()
            raw = canonical(value)
            Path(
                config["roots"]["tool"], "runtime/portrait/inventory.json"
            ).write_bytes(raw)
            config["inventory_sha256"] = hashlib.sha256(raw).hexdigest()
            write_descriptor(config, value)
            with self.assertRaises(BundleError):
                _resolve_bundle(config)

    def test_directory_alias_is_bounded_declared_and_same_root(self):
        from server_app.portraits.bundle import BundleError, directory_aliases

        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            target, alias = root / "lib", root / "lib64"
            target.mkdir()
            alias.mkdir()
            original = Path.is_symlink
            with (
                patch.object(Path, "is_symlink", lambda p: p == alias or original(p)),
                patch.object(Path, "readlink", lambda p: Path("lib")),
            ):
                result = directory_aliases(
                    {"python": root},
                    [
                        {
                            "role": "python",
                            "path": "lib64",
                            "target_role": "python",
                            "target_path": "lib",
                        }
                    ],
                )
                self.assertEqual(result[alias], target)
                with self.assertRaises(BundleError):
                    directory_aliases(
                        {"python": root},
                        [
                            {
                                "role": "python",
                                "path": "lib64",
                                "target_role": "tool",
                                "target_path": "lib",
                            }
                        ],
                    )

    def test_python_process_requires_complete_base_prefix_and_loaded_files(self):
        from types import SimpleNamespace

        from server_app.portraits.bundle import BundleError, verify_python_process

        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            venv = root / "venv"
            (venv / "bin").mkdir(parents=True)
            (venv / "pyvenv.cfg").write_text("include-system-site-packages = false\n")
            binary, module = venv / "bin/python", root / "os.py"
            binary.write_bytes(b"python")
            module.write_bytes(b"pass")
            manifest = tuple(
                (str(p), hashlib.sha256(p.read_bytes()).hexdigest())
                for p in (binary, module, venv / "pyvenv.cfg")
            )
            runtime = SimpleNamespace(python=binary, runtime_manifest=manifest)
            verified = SimpleNamespace(source_runtime=runtime)
            process = SimpleNamespace(
                base_prefix=str(root),
                prefix=str(venv),
                executable=str(binary),
                path=[str(root)],
                modules={"os": SimpleNamespace(__file__=str(module))},
                version_info=(3, 12),
            )
            with (
                patch("server_app.portraits.bundle.sys", process),
                patch("site.ENABLE_USER_SITE", False),
                patch("server_app.portraits.bundle.verify_python_metadata"),
            ):
                verify_python_process(
                    {"roots": {"python": str(root)}, "host_files": {}},
                    verified,
                    {"directory_aliases": [], "host_files": {}},
                )
                process.base_prefix = str(root / "site-packages")
                with self.assertRaises(BundleError):
                    verify_python_process(
                        {"roots": {"python": str(root)}, "host_files": {}},
                        verified,
                        {"directory_aliases": [], "host_files": {}},
                    )

    @unittest.skipUnless(os.name == "posix", "requires real POSIX directory aliases")
    def test_real_directory_alias_posix(self):
        from server_app.portraits.bundle import declared_path, directory_aliases

        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            (root / "lib").mkdir()
            (root / "lib/module.py").write_bytes(b"pass")
            (root / "lib64").symlink_to("lib", target_is_directory=True)
            aliases = directory_aliases(
                {"python": root},
                [
                    {
                        "role": "python",
                        "path": "lib64",
                        "target_role": "python",
                        "target_path": "lib",
                    }
                ],
            )
            self.assertEqual(
                declared_path(root / "lib64/module.py", aliases), root / "lib/module.py"
            )

    def test_python_wheel_metadata_rejects_cp314_and_wrong_unitypy(self):
        from server_app.portraits.bundle import BundleError, verify_python_metadata

        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            info = root / "UnityPy-1.25.0.dist-info"
            info.mkdir()
            (root / "UnityPy").mkdir()
            (root / "UnityPy/__init__.py").write_text("")
            (info / "METADATA").write_text(
                "Name: UnityPy\nVersion: 1.25.0\nRequires-Python: >=3.8\n"
            )
            (info / "WHEEL").write_text("Wheel-Version: 1.0\nTag: py3-none-any\n")
            import base64
            import csv

            with (info / "RECORD").open("w", newline="") as stream:
                writer = csv.writer(stream)
                for file in (
                    info / "METADATA",
                    info / "WHEEL",
                    root / "UnityPy/__init__.py",
                ):
                    raw = file.read_bytes()
                    writer.writerow(
                        [
                            file.relative_to(root).as_posix(),
                            "sha256="
                            + base64.urlsafe_b64encode(hashlib.sha256(raw).digest())
                            .decode()
                            .rstrip("="),
                            len(raw),
                        ]
                    )
                writer.writerow(["UnityPy-1.25.0.dist-info/RECORD", "", ""])
            verify_python_metadata(root, (3, 12))
            alias = root / "lib64"
            alias_metadata = alias / info.name / "METADATA"
            original_resolve = Path.resolve
            with (
                patch.object(
                    Path,
                    "glob",
                    lambda path, pattern: iter(
                        [info / "METADATA"] if path == root else [alias_metadata]
                    ),
                ),
                patch.object(
                    Path,
                    "resolve",
                    lambda path, *args, **kwargs: (
                        info / "METADATA"
                        if path == alias_metadata
                        else original_resolve(path, *args, **kwargs)
                    ),
                ),
            ):
                verify_python_metadata(root, (3, 12), [root, alias])
            (info / "WHEEL").write_text(
                "Wheel-Version: 1.0\nTag: cp314-cp314-manylinux_2_28_x86_64\n"
            )
            with self.assertRaises(BundleError):
                verify_python_metadata(root, (3, 12))

    def test_python_frontier_rejects_pth_escape_and_active_bytecode_zip(self):
        import zipfile

        from server_app.portraits.bundle import BundleError
        from server_app.portraits.bundle_python import verify_python_frontier

        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            (root / "unsafe.pth").write_text("/outside/unverified\n")
            with self.assertRaises(BundleError):
                verify_python_frontier(root, [str(root)], {}, {})
            (root / "unsafe.pth").write_text("import unsafe_hook\n")
            with self.assertRaises(BundleError):
                verify_python_frontier(root, [str(root)], {}, {})
            (root / "unsafe.pth").write_text("# inert\n")
            archive = root / "stdlib.zip"
            with zipfile.ZipFile(archive, "w") as stream:
                stream.writestr("module.pyc", b"compiled")
            manifest = {str(archive): hashlib.sha256(archive.read_bytes()).hexdigest()}
            with self.assertRaises(BundleError):
                verify_python_frontier(root, [str(archive)], {}, manifest)

    def test_schema_requires_integer_one(self):
        from server_app.portraits.bundle import BundleError, _resolve_bundle

        with tempfile.TemporaryDirectory() as temp:
            config, value = fixture(Path(temp))
            for schema in (True, 1.0, "1", 2):
                with self.subTest(schema=schema):
                    value["schema"] = schema
                    raw = canonical(value)
                    Path(
                        config["roots"]["tool"], "runtime/portrait/inventory.json"
                    ).write_bytes(raw)
                    config["inventory_sha256"] = hashlib.sha256(raw).hexdigest()
                    with self.assertRaises(BundleError):
                        _resolve_bundle(config)

    def test_production_source_recipe_cannot_omit_unloaded_tool_code(self):
        from types import SimpleNamespace

        from server_app.portraits.bundle import (
            SOURCE_DIRECTORIES,
            SOURCE_FILES,
            BundleError,
            verify_source_recipe,
        )

        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            roots = {role: root / role for role in ("server", "tool")}
            paths = []
            for role, source_root in roots.items():
                for directory in SOURCE_DIRECTORIES[role]:
                    folder = source_root / directory
                    folder.mkdir(parents=True, exist_ok=True)
                    path = folder / "module.py"
                    path.write_bytes(b"pass")
                    paths.append(path)
                for name in SOURCE_FILES[role]:
                    path = source_root / name
                    path.parent.mkdir(parents=True, exist_ok=True)
                    path.write_bytes(b"pass")
                    paths.append(path)
            runtime = SimpleNamespace(
                runtime_manifest=tuple(
                    (str(path), hashlib.sha256(path.read_bytes()).hexdigest())
                    for path in paths
                )
            )
            verified = SimpleNamespace(source_runtime=runtime)
            verify_source_recipe({"roots": roots}, verified)
            (roots["tool"] / "app/features/preview/lazy.py").write_bytes(b"pass")
            with self.assertRaises(BundleError):
                verify_source_recipe({"roots": roots}, verified)

    def test_resolver_exists(self):
        self.assertIsNotNone(importlib.util.find_spec("server_app.portraits.bundle"))

    def test_noncanonical_and_duplicate_json_rejected(self):
        from server_app.portraits.bundle import BundleError, strict_json

        for raw in (b'{"x":1,"x":2}', b'{"x":NaN}', b'{ "x":1}'):
            with self.subTest(raw=raw), self.assertRaises(BundleError):
                strict_json(raw)

    def test_portable_names(self):
        from server_app.portraits.bundle import BundleError, portable_path

        for value in (
            "../x",
            "/x",
            "C:/x",
            "x\\y",
            "a//b",
            "a/./b",
            "a:",
            "a\nb",
            "x?y",
            "CON",
            "",
        ):
            with self.subTest(value=value), self.assertRaises(BundleError):
                portable_path(value)
        self.assertEqual(
            portable_path("runtime/portrait/a.dll"), "runtime/portrait/a.dll"
        )

    def test_hash_and_escape_and_extra_fail_closed(self):
        from server_app.portraits.bundle import BundleError, verify_inventory

        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            roots = {
                role: root / role
                for role in (
                    "server",
                    "tool",
                    "python",
                    "dotnet",
                    "java",
                    "native",
                    "display",
                )
            }
            for path in roots.values():
                path.mkdir()
            file = roots["server"] / "a.py"
            file.write_bytes(b"pass\n")
            entry = {
                "role": "server",
                "path": "a.py",
                "size": 5,
                "sha256": hashlib.sha256(file.read_bytes()).hexdigest(),
                "kind": "source",
            }
            entries = [entry]
            self.assertEqual(len(verify_inventory(roots, entries)), 1)
            file.write_bytes(b"fail\n")
            with self.assertRaises(BundleError):
                verify_inventory(roots, entries)
            file.write_bytes(b"pass\n")
            (roots["native"] / "unlisted.so").write_bytes(b"unknown")
            with self.assertRaises(BundleError):
                verify_inventory(roots, entries)

    def test_unknown_binary_rejected(self):
        from server_app.portraits.bundle import BundleError, verify_inventory

        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            roots = {
                role: root / role
                for role in (
                    "server",
                    "tool",
                    "python",
                    "dotnet",
                    "java",
                    "native",
                    "display",
                )
            }
            for path in roots.values():
                path.mkdir()
            data = b"\x7fELF" + b"\0" * 100
            (roots["native"] / "x.so").write_bytes(data)
            entry = {
                "role": "native",
                "path": "x.so",
                "size": len(data),
                "sha256": hashlib.sha256(data).hexdigest(),
                "kind": "data",
            }
            with self.assertRaises(BundleError):
                verify_inventory(roots, [entry])

    def test_elf_unknown_and_new_abi_rejected(self):
        from server_app.portraits.bundle import BundleError, elf_identity

        for data in (
            b"not elf",
            b"\x7fELF" + b"\0" * 100,
            elf() + b"GLIBC_2.29\0",
            elf() + b"GLIBCXX_3.4.26\0",
        ):
            with self.assertRaises(BundleError):
                elf_identity(data)


if __name__ == "__main__":
    unittest.main()
