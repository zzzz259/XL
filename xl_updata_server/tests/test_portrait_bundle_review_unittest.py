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
    def _origin_fixture(self, root, needed="$ORIGIN/../lib/libpython3.12.so.1.0"):
        config, value = fixture(root)
        add(
            config,
            value,
            "python",
            "lib/libpython3.12.so.1.0",
            elf(soname="libpython3.12.so.1.0"),
            "elf",
        )
        # Independent expected ABI ensures RED reaches the resolver itself.
        ref = add(config, value, "python", "lib/libpython3.so", elf(), "elf")
        raw = elf(needed=(needed,))
        Path(config["roots"]["python"], ref[1]).write_bytes(raw)
        entry = next(e for e in value["entries"] if [e["role"], e["path"]] == ref)
        entry.update(size=len(raw), sha256=hashlib.sha256(raw).hexdigest())
        entry["abi"]["needed"] = [needed]
        seal(config, value)
        return config, value

    def test_private_python_origin_needed_and_relocation(self):
        for needed in (
            "$ORIGIN/../lib/libpython3.12.so.1.0",
            "${ORIGIN}/./libpython3.12.so.1.0",
        ):
            with self.subTest(needed=needed), tempfile.TemporaryDirectory() as temp:
                first, _ = self._origin_fixture(Path(temp) / "first", needed)
                second, _ = self._origin_fixture(Path(temp) / "second", needed)
                self.assertEqual(
                    _resolve_bundle(first).tool_fingerprint,
                    _resolve_bundle(second).tool_fingerprint,
                )

    def test_origin_needed_rejects_lexical_ambiguity_and_soname(self):
        for name in (
            "../lib.so",
            "/lib.so",
            "C:/lib.so",
            "$LIB/lib.so",
            "$ORIGIN",
            "$ORIGIN//lib.so",
            "$ORIGIN/",
            "$ORIGIN/..",
            "$ORIGIN/a\\b",
            "$ORIGIN/a:b",
            "$ORIGIN/a\n",
            "$ORIGIN/${LIB}/lib.so",
            "$ORIGIN/" + "a/" * 64 + "b",
        ):
            with self.subTest(name=name), self.assertRaises(BundleError):
                elf_identity(elf(needed=(name,)))
        for name in ("$ORIGIN/lib.so", "${ORIGIN}/lib.so", "$FOO", "a/b"):
            with self.subTest(soname=name), self.assertRaises(BundleError):
                elf_identity(elf(soname=name))

    def test_origin_needed_rejects_escape_missing_cross_role_and_partial_prefix(self):
        for needed in (
            "$ORIGIN/../../native/libpython3.12.so.1.0",
            "$ORIGIN/missing/libpython3.12.so.1.0",
        ):
            with self.subTest(needed=needed), tempfile.TemporaryDirectory() as temp:
                config, _ = self._origin_fixture(Path(temp), needed)
                with self.assertRaisesRegex(BundleError, "ORIGIN NEEDED"):
                    _resolve_bundle(config)
        with tempfile.TemporaryDirectory() as temp:
            config, value = self._origin_fixture(Path(temp))
            value["source_scopes"]["python"] = ["lib"]
            seal(config, value)
            with self.assertRaisesRegex(BundleError, "source closure"):
                _resolve_bundle(config)

    def test_origin_needed_target_identity_and_recursive_closure(self):
        for mutation in ("hash", "abi", "unlisted", "recursive"):
            with self.subTest(mutation=mutation), tempfile.TemporaryDirectory() as temp:
                config, value = self._origin_fixture(Path(temp))
                ref = ["python", "lib/libpython3.12.so.1.0"]
                entry = next(
                    e for e in value["entries"] if [e["role"], e["path"]] == ref
                )
                if mutation == "hash":
                    Path(config["roots"]["python"], ref[1]).write_bytes(
                        elf() + b"tamper"
                    )
                elif mutation == "abi":
                    entry["abi"]["soname"] = "wrong.so"
                elif mutation == "unlisted":
                    value["entries"].remove(entry)
                else:
                    replace_elf(config, value, ref, elf(needed=("absent.so",)))
                seal(config, value)
                with self.assertRaises(BundleError):
                    _resolve_bundle(config)

    def test_origin_needed_has_no_basename_environment_fallback_or_shadow(self):
        with tempfile.TemporaryDirectory() as temp:
            config, value = self._origin_fixture(Path(temp))
            # This unlisted host sibling is in the real Python loader environment.
            host = Path(config["host_files"]["env"]).parent
            (host / "libpython3.12.so.1.0").write_bytes(elf() + b"shadow")
            native = ["host", "env"]
            value["descriptor_groups"]["native_files"] = [native]
            config["loader_dirs"] = value["loader_dirs"] = [
                ["host", "env"],
                *value["loader_dirs"],
            ]
            seal(config, value)
            self.assertTrue(_resolve_bundle(config).tool_fingerprint)
            replace_elf(
                config,
                value,
                ["python", "lib/libpython3.so"],
                elf(needed=("$ORIGIN/missing/libpython3.12.so.1.0",)),
            )
            seal(config, value)
            with self.assertRaisesRegex(BundleError, "ORIGIN NEEDED"):
                _resolve_bundle(config)

    def test_origin_needed_rejects_copied_and_non_python_contexts(self):
        for ref in (
            ["display", "xkbcomp"],
            ["display", "Xvfb"],
            ["dotnet", "dotnet"],
            ["java", "java"],
            ["native", "prlimit"],
        ):
            with self.subTest(ref=ref), tempfile.TemporaryDirectory() as temp:
                config, value = fixture(Path(temp))
                replace_elf(config, value, ref, elf(needed=("$ORIGIN/target.so",)))
                seal(config, value)
                with self.assertRaisesRegex(BundleError, "private Python"):
                    _resolve_bundle(config)
        with tempfile.TemporaryDirectory() as temp:
            config, value = self._origin_fixture(Path(temp))
            value["descriptor_groups"]["runtime_files"].append(
                ["python", "lib/libpython3.so"]
            )
            value["descriptor_groups"]["runtime_files"].sort()
            seal(config, value)
            with self.assertRaisesRegex(BundleError, "source context"):
                _resolve_bundle(config)

    def test_origin_needed_declared_directory_and_file_aliases(self):
        for alias_kind in ("directory", "file", "executable"):
            with (
                self.subTest(alias_kind=alias_kind),
                tempfile.TemporaryDirectory() as temp,
            ):
                config, value = self._origin_fixture(Path(temp))
                root = Path(config["roots"]["python"])
                if alias_kind == "directory":
                    (root / "lib64").symlink_to("lib", target_is_directory=True)
                    value["directory_aliases"] = [
                        {
                            "role": "python",
                            "path": "lib64",
                            "target_role": "python",
                            "target_path": "lib",
                        }
                    ]
                    needed = "$ORIGIN/../lib64/libpython3.12.so.1.0"
                else:
                    name = (
                        "python"
                        if alias_kind == "executable"
                        else "lib/libpython3.12.so.1.0"
                    )
                    if alias_kind == "executable":
                        replace_elf(
                            config,
                            value,
                            ["python", "python"],
                            elf(needed=("$ORIGIN/lib/libpython3.12.so.1.0",)),
                        )
                    target = root / name
                    alias = root / (
                        "python-alias" if alias_kind == "executable" else "lib/alias.so"
                    )
                    alias.symlink_to(target)
                    entry = next(
                        e
                        for e in value["entries"]
                        if e["role"] == "python" and e["path"] == name
                    )
                    evidence = {
                        k: entry[k]
                        for k in ("size", "sha256", "abi", "platform", "provenance")
                    }
                    config["host_files"].update(
                        py_actual=str(target), py_alias=str(alias)
                    )
                    value["host_files"].update(
                        py_actual=evidence,
                        py_alias={**evidence, "alias_of": "py_actual"},
                    )
                    needed = "$ORIGIN/alias.so"
                    if alias_kind == "executable":
                        config["entrypoints"]["python"] = ["host", "py_alias"]
                        value["entrypoints"]["python"] = ["host", "py_alias"]
                        needed = "$ORIGIN/../lib/libpython3.12.so.1.0"
                replace_elf(
                    config,
                    value,
                    ["python", "lib/libpython3.so"],
                    elf(needed=(needed,)),
                )
                seal(config, value)
                self.assertTrue(_resolve_bundle(config).tool_fingerprint)
                if alias_kind == "directory":
                    value["directory_aliases"] = []
                else:
                    value["host_files"].pop("py_alias")
                    config["host_files"].pop("py_alias")
                seal(config, value)
                with self.assertRaises(BundleError):
                    _resolve_bundle(config)

    def test_origin_needed_does_not_normalize_away_missing_or_file_components(self):
        for middle in ("missing", "libpython3.12.so.1.0"):
            with self.subTest(middle=middle), tempfile.TemporaryDirectory() as temp:
                config, _ = self._origin_fixture(
                    Path(temp), "$ORIGIN/" + middle + "/../libpython3.12.so.1.0"
                )
                with self.assertRaises(BundleError):
                    _resolve_bundle(config)

    def test_origin_needed_uses_actual_target_not_soname_index(self):
        with tempfile.TemporaryDirectory() as temp:
            config, value = self._origin_fixture(Path(temp))
            replace_elf(
                config,
                value,
                ["python", "lib/libpython3.12.so.1.0"],
                elf(soname="different-soname.so"),
            )
            seal(config, value)
            self.assertTrue(_resolve_bundle(config).tool_fingerprint)

    def test_origin_needed_alias_parent_uses_real_directory(self):
        with tempfile.TemporaryDirectory() as temp:
            config, value = self._origin_fixture(
                Path(temp), "$ORIGIN/../link/../libpython3.12.so.1.0"
            )
            root = Path(config["roots"]["python"])
            (root / "lib/sub").mkdir()
            (root / "link").symlink_to("lib/sub", target_is_directory=True)
            value["directory_aliases"] = [
                {
                    "role": "python",
                    "path": "link",
                    "target_role": "python",
                    "target_path": "lib/sub",
                }
            ]
            seal(config, value)
            self.assertTrue(_resolve_bundle(config).tool_fingerprint)

    def _dso_alias_origin_fixture(
        self,
        root,
        tag,
        *,
        equivalent=False,
        shadow=False,
        incoming="direct",
        cross_role=False,
    ):
        config, value = fixture(root)
        python_root = Path(config["roots"]["python"])
        add(config, value, "python", "real/child.so", elf(soname="child.so"), "elf")
        if tag == "needed":
            raw = elf(needed=("$ORIGIN/child.so",), soname="origin-a.so")
        elif tag == "rpath":
            raw = rpath_elf(needed=("child.so",), soname="origin-a.so", rpath="$ORIGIN")
        else:
            raw = elf(needed=("child.so",), soname="origin-a.so", runpath="${ORIGIN}")
        ref = add(config, value, "python", "real/a.so", raw, "elf")
        if equivalent:
            (python_root / "alias").symlink_to("real", target_is_directory=True)
            value["directory_aliases"] = [
                {
                    "role": "python",
                    "path": "alias",
                    "target_role": "python",
                    "target_path": "real",
                }
            ]
            alias = python_root / "real/alias-a.so"
        else:
            (python_root / "alias").mkdir()
            alias = python_root / "alias/alias-a.so"
        if incoming == "soname":
            alias = alias.with_name("origin-a.so")
        if cross_role:
            alias = Path(config["roots"]["native"]) / alias.name
        target = python_root / "real/a.so"
        alias.symlink_to(target)
        entry = next(e for e in value["entries"] if [e["role"], e["path"]] == ref)
        evidence = {
            k: entry[k] for k in ("size", "sha256", "abi", "platform", "provenance")
        }
        config["host_files"].update(dso_actual=str(target), dso_alias=str(alias))
        value["host_files"].update(
            dso_actual=evidence, dso_alias={**evidence, "alias_of": "dso_actual"}
        )
        if shadow:
            add(
                config,
                value,
                "python",
                "alias/child.so",
                elf(soname="different-child.so"),
                "elf",
            )
        if incoming == "soname":
            main = rpath_elf(needed=("origin-a.so",), rpath="$ORIGIN/alias")
        elif incoming == "late":
            main = elf()
        else:
            main = elf(needed=("$ORIGIN/alias/alias-a.so",))
        replace_elf(config, value, ["python", "python"], main)
        seal(config, value)
        return config

    def test_cross_directory_dso_alias_rejects_missing_load_origin_child(self):
        for tag in ("needed", "rpath", "runpath"):
            with self.subTest(tag=tag), tempfile.TemporaryDirectory() as temp:
                config = self._dso_alias_origin_fixture(Path(temp), tag)
                with self.assertRaisesRegex(BundleError, "DSO.*origin"):
                    _resolve_bundle(config)

    def test_cross_directory_dso_alias_rejects_different_load_origin_child(self):
        for tag in ("needed", "rpath", "runpath"):
            with self.subTest(tag=tag), tempfile.TemporaryDirectory() as temp:
                config = self._dso_alias_origin_fixture(Path(temp), tag, shadow=True)
                with self.assertRaisesRegex(BundleError, "DSO.*origin"):
                    _resolve_bundle(config)

    def test_dso_alias_with_equivalent_loading_directory_is_valid(self):
        for tag in ("needed", "rpath", "runpath"):
            with self.subTest(tag=tag), tempfile.TemporaryDirectory() as temp:
                config = self._dso_alias_origin_fixture(
                    Path(temp), tag, equivalent=True
                )
                self.assertTrue(_resolve_bundle(config).tool_fingerprint)

    def test_origin_dso_soname_incoming_alias_rejected(self):
        for tag in ("needed", "rpath", "runpath"):
            for shadow in (False, True):
                with (
                    self.subTest(tag=tag, shadow=shadow),
                    tempfile.TemporaryDirectory() as temp,
                ):
                    config = self._dso_alias_origin_fixture(
                        Path(temp), tag, incoming="soname", shadow=shadow
                    )
                    with self.assertRaisesRegex(BundleError, "DSO.*origin"):
                        _resolve_bundle(config)

    def test_origin_dso_late_alias_rejected_without_incoming_edge(self):
        for tag in ("needed", "rpath", "runpath"):
            for cross_role in (False, True):
                with (
                    self.subTest(tag=tag, cross_role=cross_role),
                    tempfile.TemporaryDirectory() as temp,
                ):
                    config = self._dso_alias_origin_fixture(
                        Path(temp), tag, incoming="late", cross_role=cross_role
                    )
                    with self.assertRaisesRegex(BundleError, "DSO.*origin"):
                        _resolve_bundle(config)

    def test_origin_dso_equivalent_alias_soname_and_late_are_valid(self):
        for tag in ("needed", "rpath", "runpath"):
            for incoming in ("soname", "late"):
                with (
                    self.subTest(tag=tag, incoming=incoming),
                    tempfile.TemporaryDirectory() as temp,
                ):
                    config = self._dso_alias_origin_fixture(
                        Path(temp), tag, incoming=incoming, equivalent=True
                    )
                    self.assertTrue(_resolve_bundle(config).tool_fingerprint)

    def _cross_directory_python_main_alias_fixture(self, root, *, dso_incoming=False):
        config, value = self._origin_fixture(root)
        replace_elf(
            config,
            value,
            ["python", "python"],
            elf(needed=("$ORIGIN/lib/libpython3.12.so.1.0",)),
        )
        target = Path(config["roots"]["python"], "python")
        alias = Path(config["host_files"]["env"]).parent / "python"
        hop = alias.with_name("python-hop")
        hop.symlink_to(target)
        alias.symlink_to(hop)
        entry = next(
            e
            for e in value["entries"]
            if e["role"] == "python" and e["path"] == "python"
        )
        evidence = {
            k: entry[k] for k in ("size", "sha256", "abi", "platform", "provenance")
        }
        config["host_files"].update(
            main_actual=str(target), main_hop=str(hop), main_alias=str(alias)
        )
        value["host_files"].update(
            main_actual=evidence,
            main_hop={**evidence, "alias_of": "main_actual"},
            main_alias={**evidence, "alias_of": "main_hop"},
        )
        config["entrypoints"]["python"] = ["host", "main_alias"]
        value["entrypoints"]["python"] = ["host", "main_alias"]
        if dso_incoming:
            add(
                config,
                value,
                "python",
                "lib/gateway.so",
                rpath_elf(needed=("python",), rpath="$ORIGIN/../../host"),
                "elf",
            )
        seal(config, value)
        return config

    def test_origin_main_executable_cross_directory_alias_is_valid(self):
        with tempfile.TemporaryDirectory() as temp:
            config = self._cross_directory_python_main_alias_fixture(Path(temp))
            self.assertTrue(_resolve_bundle(config).tool_fingerprint)

    def test_origin_main_alias_cannot_be_used_as_dso_exception(self):
        with tempfile.TemporaryDirectory() as temp:
            config = self._cross_directory_python_main_alias_fixture(
                Path(temp), dso_incoming=True
            )
            with self.assertRaisesRegex(BundleError, "DSO.*origin"):
                _resolve_bundle(config)

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
