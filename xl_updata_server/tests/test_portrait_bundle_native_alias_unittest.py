"""Actual OS alias evidence through the whole bundle resolver."""

import hashlib
import json
import os
import tempfile
import unittest
from pathlib import Path

from server_app.portraits.bundle import BundleError, _resolve_bundle, elf_identity
from test_portrait_bundle_unittest import canonical, elf, fixture, write_descriptor


def native_alias_fixture(root, *, hops=1, mode="valid"):
    config, value = fixture(root)
    native = Path(config["roots"]["native"])
    target = native / "libcsfml-graphics.so.2.6.1"
    data = elf(soname="libcsfml-graphics.so.2.6")
    target.write_bytes(data)
    evidence = {
        "size": len(data),
        "sha256": hashlib.sha256(data).hexdigest(),
        "abi": elf_identity(data),
        "platform": "linux-x86_64",
        "provenance": "synthetic shared SONAME fixture",
    }
    entry = dict(evidence, role="native", path=target.name, kind="elf")
    value["entries"].append(entry)
    value["entries"].sort(key=lambda row: (row["role"], row["path"]))
    value["descriptor_groups"]["native_files"] = [["native", target.name]]
    # Native descriptor order establishes the actual managed environment.
    config["loader_dirs"] = [["native", ""], ["native", "dri"]]
    value["loader_dirs"] = config["loader_dirs"]
    value["host_files"]["provider"] = evidence.copy()
    config["host_files"]["provider"] = str(target)
    previous = "provider"
    for index, name in enumerate(("libcsfml-graphics.so.2.6", "libcsfml-graphics.so")):
        path = native / name
        dest = Path(config["host_files"][previous if hops == 2 else "provider"])
        path.symlink_to(dest.name)
        value["host_files"][name] = dict(
            evidence, alias_of=previous if hops == 2 else "provider"
        )
        config["host_files"][name] = str(path)
        previous = name
    if mode == "unlisted":
        del value["host_files"][previous]
        del config["host_files"][previous]
    elif mode == "wrong-hop":
        value["host_files"][previous]["alias_of"] = "provider"
    elif mode == "non-native":
        value["descriptor_groups"]["native_files"] = []
        config["loader_dirs"] = [["native", "dri"], ["native", ""]]
        value["loader_dirs"] = config["loader_dirs"]
    elif mode == "cross-directory":
        path = native / "libcsfml-graphics.so"
        path.unlink()
        path = native / "dri" / path.name
        path.symlink_to("../" + target.name)
        config["host_files"][path.name] = str(path)
    elif mode in ("other-target", "duplicate-provider"):
        other = native / "other.so"
        other.write_bytes(
            data if mode == "duplicate-provider" else elf(soname="other.so")
        )
        other_data = other.read_bytes()
        other_entry = dict(
            evidence,
            role="native",
            path=other.name,
            kind="elf",
            size=len(other_data),
            sha256=hashlib.sha256(other_data).hexdigest(),
            abi=elf_identity(other_data),
        )
        value["entries"].append(other_entry)
        value["entries"].sort(key=lambda row: (row["role"], row["path"]))
        if mode == "other-target":
            path = native / "libcsfml-graphics.so"
            path.unlink()
            path.symlink_to(other.name)
            value["host_files"][path.name] = {key: other_entry[key] for key in evidence}
            value["host_files"][path.name]["alias_of"] = "other"
            value["host_files"]["other"] = {key: other_entry[key] for key in evidence}
            config["host_files"]["other"] = str(other)
    elif mode == "wrong-first-provider":
        shadow = native / "dri/libcsfml-graphics.so"
        shadow.symlink_to(Path(config["host_files"]["unshare"]))
        value["host_files"]["shadow"] = dict(
            value["host_files"]["unshare"], alias_of="unshare"
        )
        config["host_files"]["shadow"] = str(shadow)
        value["descriptor_groups"]["native_files"].insert(
            0, ["native", "dri/swrast_dri.so"]
        )
        config["loader_dirs"] = [["native", "dri"], ["native", ""]]
        value["loader_dirs"] = config["loader_dirs"]
    elif mode == "wrong-bytes":
        value["host_files"][previous]["sha256"] = "0" * 64
    for row in value["entries"]:
        if row["path"].endswith(".deps.json"):
            path = Path(config["roots"][row["role"]]) / row["path"]
            deps = json.loads(path.read_bytes())
            deps["targets"]["net8.0"]["fixture/1"]["native"] = {
                "libcsfml-graphics.so": {},
                "libcsfml-graphics.so.2.6": {},
            }
            raw = canonical(deps)
            path.write_bytes(raw)
            row.update(size=len(raw), sha256=hashlib.sha256(raw).hexdigest())
    raw = canonical(value)
    Path(config["roots"]["tool"], "runtime/portrait/inventory.json").write_bytes(raw)
    config["inventory_sha256"] = hashlib.sha256(raw).hexdigest()
    write_descriptor(config, value)
    return config


@unittest.skipUnless(os.name == "posix", "requires real OS symlinks; no alias mocks")
class NativeAliasTests(unittest.TestCase):
    def test_declared_same_directory_aliases_and_relocation(self):
        for hops in (1, 2):
            with self.subTest(hops=hops), tempfile.TemporaryDirectory() as tmp:
                first = _resolve_bundle(
                    native_alias_fixture(Path(tmp) / "a", hops=hops)
                )
                second = _resolve_bundle(
                    native_alias_fixture(Path(tmp) / "b", hops=hops)
                )
                self.assertEqual(first.tool_fingerprint, second.tool_fingerprint)

    def test_invalid_aliases_rejected(self):
        for mode in (
            "unlisted",
            "wrong-hop",
            "non-native",
            "cross-directory",
            "wrong-bytes",
            "other-target",
            "duplicate-provider",
            "wrong-first-provider",
        ):
            with self.subTest(mode=mode), tempfile.TemporaryDirectory() as tmp:
                config = native_alias_fixture(Path(tmp), hops=2, mode=mode)
                with self.assertRaises(BundleError):
                    _resolve_bundle(config)

    def test_external_parent_alias_is_optional_not_admissible(self):
        for required in (False, True):
            with self.subTest(required=required), tempfile.TemporaryDirectory() as tmp:
                root = Path(tmp)
                config = native_alias_fixture(root)
                inventory = (
                    Path(config["roots"]["tool"]) / "runtime/portrait/inventory.json"
                )
                value = json.loads(inventory.read_bytes())
                actual_dir = root / "outside"
                actual_dir.mkdir()
                directory_link = root / "outside-link"
                directory_link.symlink_to(actual_dir, target_is_directory=True)
                target = Path(config["host_files"]["provider"])
                (actual_dir / "optional.so").symlink_to(target)
                config["host_files"]["optional"] = str(directory_link / "optional.so")
                value["host_files"]["optional"] = dict(
                    value["host_files"]["provider"], alias_of="provider"
                )
                if required:
                    for row in value["entries"]:
                        if row["path"].endswith(".deps.json"):
                            path = Path(config["roots"][row["role"]]) / row["path"]
                            deps = json.loads(path.read_bytes())
                            deps["targets"]["net8.0"]["fixture/1"]["native"][
                                "optional.so"
                            ] = {}
                            raw = canonical(deps)
                            path.write_bytes(raw)
                            row.update(
                                size=len(raw), sha256=hashlib.sha256(raw).hexdigest()
                            )
                raw = canonical(value)
                inventory.write_bytes(raw)
                config["inventory_sha256"] = hashlib.sha256(raw).hexdigest()
                write_descriptor(config, value)
                if required:
                    with self.assertRaisesRegex(
                        BundleError, "managed deps closure incomplete"
                    ):
                        _resolve_bundle(config)
                else:
                    _resolve_bundle(config)
