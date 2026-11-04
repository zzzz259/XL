"""Regression oracles for the complete deployment review batch (no native run)."""

import hashlib
import struct
import tempfile
import unittest
from pathlib import Path

from server_app.portraits.bundle import BundleError, _resolve_bundle, elf_identity
from test_portrait_bundle_unittest import canonical, elf, fixture, write_descriptor


def seal(config, value):
    value["entries"].sort(key=lambda e: (e["role"], e["path"]))
    raw = canonical(value)
    Path(config["roots"]["tool"], "runtime/portrait/inventory.json").write_bytes(raw)
    config["inventory_sha256"] = hashlib.sha256(raw).hexdigest()
    write_descriptor(config, value)


def add(config, value, role, name, data, kind="data"):
    path = Path(config["roots"][role], name)
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
            provenance="synthetic regression only",
            abi=elf_identity(data),
        )
    value["entries"].append(entry)
    return [role, name]


def rpath_elf(*, needed=(), soname=None, rpath):
    raw = bytearray(elf(needed=needed, soname=soname, runpath=rpath))
    section = struct.unpack_from("<IIQQQQIIQQ", raw, 192)
    for offset in range(section[4], section[4] + section[5], 16):
        if struct.unpack_from("<q", raw, offset)[0] == 29:
            struct.pack_into("<q", raw, offset, 15)
    return bytes(raw)


def replace_elf(config, value, ref, raw):
    role, name = ref
    Path(config["roots"][role], name).write_bytes(raw)
    entry = next(e for e in value["entries"] if (e["role"], e["path"]) == tuple(ref))
    entry.update(
        size=len(raw), sha256=hashlib.sha256(raw).hexdigest(), abi=elf_identity(raw)
    )


def add_host(config, value, name, raw):
    path = Path(config["host_files"]["unshare"]).parent / name
    path.write_bytes(raw)
    config["host_files"][name] = str(path)
    value["host_files"][name] = {
        "size": len(raw),
        "sha256": hashlib.sha256(raw).hexdigest(),
        "abi": elf_identity(raw),
        "platform": "linux-x86_64",
        "provenance": "synthetic loader regression",
    }
    return ["host", name]


class ReviewRegressionTests(unittest.TestCase):
    def _ancestor_fixture(
        self, root, *, runpath=False, bridge_runpath=False, late=False
    ):
        config, value = fixture(root)
        bridge = add_host(
            config, value, "libbridge_review.so", elf(needed=("libleaf_review.so",))
        )
        leaf = add(
            config,
            value,
            "native",
            "libleaf_review.so",
            elf(soname="libleaf_review.so"),
            "elf",
        )
        value["descriptor_groups"]["native_files"] = [leaf]
        builder = elf if runpath else rpath_elf
        keyword = "runpath" if runpath else "rpath"
        replace_elf(
            config,
            value,
            ["dotnet", "dotnet"],
            builder(
                needed=() if late else ("libbridge_review.so",),
                **{keyword: "$ORIGIN/../host"},
            ),
        )
        if late:
            value["descriptor_groups"]["runtime_files"].append(bridge)
            value["descriptor_groups"]["runtime_files"].sort()
        if bridge_runpath:
            raw = elf(needed=("libleaf_review.so",), runpath="$ORIGIN")
            add_host(config, value, "libbridge_review.so", raw)
        self._loader_mapping(config, value)
        seal(config, value)
        return config, value

    def test_ancestor_rpath_rejects_unlisted_first_candidate(self):
        with tempfile.TemporaryDirectory() as temp:
            config, _ = self._ancestor_fixture(Path(temp))
            (Path(temp) / "host/libleaf_review.so").write_bytes(elf() + b"shadow")
            with self.assertRaisesRegex(BundleError, "managed.*NEEDED"):
                _resolve_bundle(config)

    def test_ancestor_runpath_is_direct_only(self):
        with tempfile.TemporaryDirectory() as temp:
            config, _ = self._ancestor_fixture(Path(temp), runpath=True)
            (Path(temp) / "host/libleaf_review.so").write_bytes(elf() + b"shadow")
            self.assertTrue(_resolve_bundle(config).tool_fingerprint)

    def test_ancestor_rpath_without_shadow_is_valid(self):
        with tempfile.TemporaryDirectory() as temp:
            config, _ = self._ancestor_fixture(Path(temp) / "first")
            relocated, _ = self._ancestor_fixture(Path(temp) / "relocated")
            self.assertEqual(
                _resolve_bundle(config).tool_fingerprint,
                _resolve_bundle(relocated).tool_fingerprint,
            )

    def test_current_runpath_skips_ancestor_rpath_for_this_lookup(self):
        with tempfile.TemporaryDirectory() as temp:
            config, _ = self._ancestor_fixture(Path(temp), bridge_runpath=True)
            (Path(temp) / "host/libleaf_review.so").write_bytes(elf() + b"shadow")
            self.assertTrue(_resolve_bundle(config).tool_fingerprint)

    def test_runpath_child_does_not_erase_older_ancestor_for_grandchild(self):
        with tempfile.TemporaryDirectory() as temp:
            config, value = self._ancestor_fixture(Path(temp), bridge_runpath=True)
            replace_elf(
                config,
                value,
                ["native", "libleaf_review.so"],
                elf(needed=("liblast_review.so",), soname="libleaf_review.so"),
            )
            last = add(
                config,
                value,
                "native",
                "liblast_review.so",
                elf(soname="liblast_review.so"),
                "elf",
            )
            value["descriptor_groups"]["native_files"].append(last)
            value["descriptor_groups"]["native_files"].sort()
            seal(config, value)
            (Path(temp) / "host/liblast_review.so").write_bytes(elf() + b"shadow")
            with self.assertRaisesRegex(BundleError, "managed.*NEEDED"):
                _resolve_bundle(config)

    def test_late_clr_uses_real_main_map_rpath(self):
        with tempfile.TemporaryDirectory() as temp:
            config, _ = self._ancestor_fixture(Path(temp), late=True)
            (Path(temp) / "host/libleaf_review.so").write_bytes(elf() + b"shadow")
            with self.assertRaisesRegex(BundleError, "managed.*NEEDED"):
                _resolve_bundle(config)

    def test_late_python_and_java_use_their_own_main_map(self):
        for role in ("python", "java"):
            with self.subTest(role=role), tempfile.TemporaryDirectory() as temp:
                config, value = fixture(Path(temp))
                replace_elf(
                    config, value, [role, role], rpath_elf(rpath="$ORIGIN/../host")
                )
                add(
                    config,
                    value,
                    role,
                    "late/module.so",
                    elf(needed=("liblate_review.so",), runpath="$ORIGIN"),
                    "elf",
                )
                add(
                    config,
                    value,
                    role,
                    "late/liblate_review.so",
                    elf(soname="liblate_review.so"),
                    "elf",
                )
                (Path(temp) / "host/liblate_review.so").write_bytes(elf() + b"shadow")
                seal(config, value)
                # The current module RUNPATH suppresses main-map RPATH.
                self.assertTrue(_resolve_bundle(config).tool_fingerprint)
                replace_elf(
                    config,
                    value,
                    [role, "late/module.so"],
                    elf(needed=("liblate_review.so",)),
                )
                seal(config, value)
                with self.assertRaisesRegex(BundleError, "source " + role + ".*NEEDED"):
                    _resolve_bundle(config)

    def test_python_and_java_do_not_share_a_main_map_or_ancestor_envelope(self):
        with tempfile.TemporaryDirectory() as temp:
            config, value = fixture(Path(temp))
            replace_elf(
                config, value, ["java", "java"], rpath_elf(rpath="$ORIGIN/../host")
            )
            add(
                config,
                value,
                "python",
                "module.so",
                rpath_elf(needed=("libpython_review.so",), rpath="$ORIGIN"),
                "elf",
            )
            add(
                config,
                value,
                "python",
                "libpython_review.so",
                elf(soname="libpython_review.so"),
                "elf",
            )
            (Path(temp) / "host/libpython_review.so").write_bytes(elf() + b"shadow")
            seal(config, value)
            self.assertTrue(_resolve_bundle(config).tool_fingerprint)

    def test_shared_provider_is_rechecked_under_distinct_ancestor_chains(self):
        with tempfile.TemporaryDirectory() as temp:
            config, value = fixture(Path(temp))
            for name, search in (
                ("liba_review.so", "$ORIGIN"),
                ("libz_review.so", "$ORIGIN/../host"),
            ):
                ref = add(
                    config,
                    value,
                    "native",
                    name,
                    rpath_elf(needed=("libshared_review.so",), rpath=search),
                    "elf",
                )
                value["descriptor_groups"]["native_files"].append(ref)
            add(
                config,
                value,
                "native",
                "libshared_review.so",
                elf(needed=("libend_review.so",)),
                "elf",
            )
            add(config, value, "native", "libend_review.so", elf(), "elf")
            value["descriptor_groups"]["native_files"].sort()
            self._loader_mapping(config, value)
            seal(config, value)
            self.assertTrue(_resolve_bundle(config).tool_fingerprint)
            (Path(temp) / "host/libend_review.so").write_bytes(elf() + b"shadow")
            with self.assertRaisesRegex(BundleError, "managed.*NEEDED"):
                _resolve_bundle(config)

    def test_loader_cycles_converge_and_excessive_depth_fails_closed(self):
        for count, cycle in ((2, True), (67, False)):
            with (
                self.subTest(count=count, cycle=cycle),
                tempfile.TemporaryDirectory() as temp,
            ):
                config, value = fixture(Path(temp))
                for index in range(count):
                    needed = (
                        (f"libchain{(index + 1) % count}.so",)
                        if cycle or index + 1 < count
                        else ()
                    )
                    ref = add(
                        config,
                        value,
                        "native",
                        f"libchain{index}.so",
                        elf(needed=needed),
                        "elf",
                    )
                    if index == 0:
                        value["descriptor_groups"]["native_files"].append(ref)
                self._loader_mapping(config, value)
                seal(config, value)
                if cycle:
                    self.assertTrue(_resolve_bundle(config).tool_fingerprint)
                else:
                    with self.assertRaisesRegex(BundleError, "loader depth bound"):
                        _resolve_bundle(config)

    def test_unknown_late_caller_cannot_hide_a_competing_rpath_provider(self):
        with tempfile.TemporaryDirectory() as temp:
            config, value = fixture(Path(temp))
            caller = add(
                config,
                value,
                "dotnet",
                "latecaller.so",
                rpath_elf(rpath="$ORIGIN/../host"),
                "elf",
            )
            callee = add(
                config,
                value,
                "dotnet",
                "latecallee.so",
                elf(needed=("liblateleaf_review.so",)),
                "elf",
            )
            leaf = add(config, value, "native", "liblateleaf_review.so", elf(), "elf")
            value["descriptor_groups"]["runtime_files"] += [caller, callee]
            value["descriptor_groups"]["runtime_files"].sort()
            value["descriptor_groups"]["native_files"] = [leaf]
            self._loader_mapping(config, value)
            seal(config, value)
            self.assertTrue(_resolve_bundle(config).tool_fingerprint)
            (Path(temp) / "host/liblateleaf_review.so").write_bytes(elf() + b"shadow")
            with self.assertRaisesRegex(BundleError, "managed.*NEEDED"):
                _resolve_bundle(config)

    def test_copied_xkbcomp_rejects_both_origin_tags_and_spellings(self):
        for builder, keyword in ((elf, "runpath"), (rpath_elf, "rpath")):
            for origin in ("$ORIGIN", "${ORIGIN}"):
                with (
                    self.subTest(tag=keyword, origin=origin),
                    tempfile.TemporaryDirectory() as temp,
                ):
                    config, value = fixture(Path(temp))
                    add(
                        config,
                        value,
                        "native",
                        "helper-only/libxkb_review.so",
                        elf(soname="libxkb_review.so"),
                        "elf",
                    )
                    replace_elf(
                        config,
                        value,
                        ["display", "xkbcomp"],
                        builder(
                            needed=("libxkb_review.so",),
                            **{keyword: origin + "/../native/helper-only"},
                        ),
                    )
                    seal(config, value)
                    with self.assertRaisesRegex(BundleError, "xkbcomp.*ORIGIN"):
                        _resolve_bundle(config)

    def test_copied_xkbcomp_without_origin_uses_real_display_dirs(self):
        with tempfile.TemporaryDirectory() as temp:
            config, value = fixture(Path(temp))
            dependency = add(
                config,
                value,
                "native",
                "libxkb_review.so",
                elf(soname="libxkb_review.so"),
                "elf",
            )
            value["descriptor_groups"]["x_libraries"].append(dependency)
            value["descriptor_groups"]["x_libraries"].sort()
            replace_elf(
                config, value, ["display", "xkbcomp"], elf(needed=("libxkb_review.so",))
            )
            seal(config, value)
            self.assertTrue(_resolve_bundle(config).tool_fingerprint)

    def _display_dependency_fixture(self, root, *, display_closed):
        config, value = fixture(root)
        for role, name, raw in (
            (
                "display",
                "Xvfb",
                elf(needed=("libGLX_mesa.so.0",))
                + b"/usr/share/X11/xkb\0/usr/bin/xkbcomp\0",
            ),
            ("native", "libGLX_mesa.so.0", elf(needed=("libreview_dependency.so",))),
        ):
            Path(config["roots"][role], name).write_bytes(raw)
            entry = next(
                e for e in value["entries"] if (e["role"], e["path"]) == (role, name)
            )
            entry.update(
                size=len(raw),
                sha256=hashlib.sha256(raw).hexdigest(),
                abi=elf_identity(raw),
            )
        dependency = add(
            config,
            value,
            "native",
            "support/libreview_dependency.so",
            elf(soname="libreview_dependency.so"),
            "elf",
        )
        group = "x_libraries" if display_closed else "native_files"
        value["descriptor_groups"][group].append(dependency)
        value["descriptor_groups"][group].sort()
        self._loader_mapping(config, value)
        seal(config, value)
        return config, value

    def _loader_mapping(self, config, value):
        from pathlib import PurePosixPath

        mapping = []
        for group in ("native_files", "x_libraries"):
            for role, name in value["descriptor_groups"][group]:
                parent = PurePosixPath(name).parent.as_posix()
                row = [role, "" if parent == "." else parent]
                if row not in mapping:
                    mapping.append(row)
        config["loader_dirs"] = value["loader_dirs"] = mapping

    def test_display_transitive_native_provider_cannot_borrow_managed_dirs(self):
        with tempfile.TemporaryDirectory() as temp:
            config, _ = self._display_dependency_fixture(
                Path(temp), display_closed=False
            )
            with self.assertRaisesRegex(BundleError, "display.*NEEDED"):
                _resolve_bundle(config)

    def test_shared_provider_closes_in_both_real_loader_contexts(self):
        with tempfile.TemporaryDirectory() as temp:
            config, value = self._display_dependency_fixture(
                Path(temp), display_closed=True
            )
            self.assertTrue(_resolve_bundle(config).tool_fingerprint)
            # A shared provider being valid in display must not mark its managed
            # use verified: managed has an additional, earlier host search dir.
            value["descriptor_groups"]["native_files"] = [["host", "unshare"]]
            config["loader_dirs"] = value["loader_dirs"] = [
                ["host", "unshare"],
                *value["loader_dirs"],
            ]
            seal(config, value)
            host = Path(config["host_files"]["unshare"]).parent
            (host / "libreview_dependency.so").write_bytes(elf() + b"unlisted shadow")
            with self.assertRaises(BundleError):
                _resolve_bundle(config)

    def test_display_walks_provider_not_named_in_descriptor_group(self):
        with tempfile.TemporaryDirectory() as temp:
            config, value = self._display_dependency_fixture(
                Path(temp), display_closed=False
            )
            add(
                config,
                value,
                "native",
                "libbridge.so",
                elf(needed=("libreview_dependency.so",)),
                "elf",
            )
            raw = elf(needed=("libbridge.so",))
            Path(config["roots"]["native"], "libGLX_mesa.so.0").write_bytes(raw)
            entry = next(e for e in value["entries"] if e["path"] == "libGLX_mesa.so.0")
            entry.update(
                size=len(raw),
                sha256=hashlib.sha256(raw).hexdigest(),
                abi=elf_identity(raw),
            )
            seal(config, value)
            with self.assertRaisesRegex(BundleError, "display.*NEEDED"):
                _resolve_bundle(config)

    def test_managed_consumer_does_not_inherit_storage_role_display_environment(self):
        with tempfile.TemporaryDirectory() as temp:
            config, value = fixture(Path(temp))
            consumer = add(
                config,
                value,
                "display",
                "managed-plugin/plugin.so",
                elf(needed=("libmanageddep.so",)),
                "elf",
            )
            dependency = add(
                config,
                value,
                "native",
                "managed-support/libmanageddep.so",
                elf(soname="libmanageddep.so"),
                "elf",
            )
            value["descriptor_groups"]["native_files"] = sorted([consumer, dependency])
            self._loader_mapping(config, value)
            seal(config, value)
            self.assertTrue(_resolve_bundle(config).tool_fingerprint)

    def test_late_python_jvm_and_clr_binaries_keep_consumer_environment_checks(self):
        for role in ("python", "java", "dotnet"):
            with self.subTest(role=role), tempfile.TemporaryDirectory() as temp:
                config, value = fixture(Path(temp))
                key = add(
                    config,
                    value,
                    role,
                    "late/module.so",
                    elf(needed=("libmissinglate.so",)),
                    "elf",
                )
                if role == "dotnet":
                    value["descriptor_groups"]["runtime_files"].append(key)
                    value["descriptor_groups"]["runtime_files"].sort()
                seal(config, value)
                with self.assertRaises(BundleError):
                    _resolve_bundle(config)

    def test_host_role_shared_provider_uses_its_actual_display_and_managed_consumers(
        self,
    ):
        with tempfile.TemporaryDirectory() as temp:
            config, value = fixture(Path(temp))
            host = Path(config["host_files"]["unshare"]).parent / "libhostconsumer.so"
            raw = elf(needed=("libhostleaf.so",), soname="libhostconsumer.so")
            host.write_bytes(raw)
            value["host_files"]["review-consumer"] = {
                "size": len(raw),
                "sha256": hashlib.sha256(raw).hexdigest(),
                "abi": elf_identity(raw),
                "platform": "linux-x86_64",
                "provenance": "synthetic F1 regression",
            }
            config["host_files"]["review-consumer"] = str(host)
            leaf = add(
                config,
                value,
                "native",
                "libhostleaf.so",
                elf(soname="libhostleaf.so"),
                "elf",
            )
            value["descriptor_groups"]["x_libraries"] += [
                ["host", "review-consumer"],
                leaf,
            ]
            value["descriptor_groups"]["x_libraries"].sort()
            config["loader_dirs"] = value["loader_dirs"] = [
                ["host", "review-consumer"],
                ["native", "dri"],
                ["native", ""],
            ]
            seal(config, value)
            self.assertTrue(_resolve_bundle(config).tool_fingerprint)

    def test_worker_import_roles_cannot_open_native_or_display_roots(self):
        with tempfile.TemporaryDirectory() as temp:
            config, value = fixture(Path(temp))
            config["dependency_roles"] = value["dependency_roles"] = ["display"]
            seal(config, value)
            with self.assertRaises(BundleError):
                _resolve_bundle(config)

    def test_active_roots_reject_lazy_package_single_file_and_namespace_shadow(self):
        for name in (
            "PIL/__init__.py",
            "json.py",
            "future_namespace/member.py",
            "empty_namespace/",
        ):
            with self.subTest(name=name), tempfile.TemporaryDirectory() as temp:
                config, _ = fixture(Path(temp))
                _resolve_bundle(config)
                shadow = Path(config["roots"]["server"], name)
                if name.endswith("/"):
                    shadow.mkdir()
                else:
                    shadow.parent.mkdir(parents=True, exist_ok=True)
                    shadow.write_bytes(b"raise AssertionError('must never import')")
                with self.assertRaisesRegex(BundleError, "unlisted|namespace|closure"):
                    _resolve_bundle(config)

    def test_needed_complete_bundle_without_dynamic_selection_is_unready(self):
        with tempfile.TemporaryDirectory() as temp:
            config, value = fixture(Path(temp))
            value.pop("dynamic_profile", None)
            seal(config, value)
            with self.assertRaises(BundleError):
                _resolve_bundle(config)

    def test_copied_app_elf_cannot_rely_on_original_origin(self):
        with tempfile.TemporaryDirectory() as temp:
            config, value = fixture(Path(temp))
            app = add(
                config,
                value,
                "tool",
                "runtime/portrait/managed/liba.so",
                elf(needed=("libb.so",), runpath="$ORIGIN/../deps"),
                "elf",
            )
            add(
                config,
                value,
                "tool",
                "runtime/portrait/deps/libb.so",
                elf(soname="libb.so"),
                "elf",
            )
            value["source_scopes"]["tool"].append("runtime/portrait/deps")
            value["descriptor_groups"]["app_files"].append(app)
            value["descriptor_groups"]["app_files"].sort()
            name = "runtime/portrait/managed/SpineViewerCLI.deps.json"
            entry = next(e for e in value["entries"] if e["path"] == name)
            path = Path(config["roots"]["tool"], name)
            import json

            deps = json.loads(path.read_bytes())
            deps["targets"]["net8.0"]["fixture/1"]["native"] = {"liba.so": {}}
            raw = canonical(deps)
            path.write_bytes(raw)
            entry.update(size=len(raw), sha256=hashlib.sha256(raw).hexdigest())
            seal(config, value)
            # The old resolver finds libb at the source ORIGIN, but staging only
            # copies app_files basenames into managed-app, never ../deps/libb.
            self.assertTrue(
                Path(config["roots"]["tool"], "runtime/portrait/deps/libb.so").is_file()
            )
            with self.assertRaisesRegex(BundleError, "app.*native|app.*ELF|staged"):
                _resolve_bundle(config)
