"""Explicit host directory witnesses through the complete resolver."""

import contextlib
import hashlib
import io
import json
import os
import tempfile
import unittest
from pathlib import Path

from server_app.portraits import bundle
from server_app.portraits.bundle import (
    BundleError,
    _resolve_bundle,
    directory_aliases,
    elf_identity,
)
from test_portrait_bundle_unittest import canonical, elf, fixture, write_descriptor


def host_fixture(root, mode="valid"):
    config, value = fixture(root)
    real = root / "system/usr/lib64"
    real.mkdir(parents=True)
    alias = root / "system/lib64"
    alias.symlink_to("usr/lib64", target_is_directory=True)
    data = elf(soname="libfixture.so.1")
    (real / "libfixture-1.so").write_bytes(data)
    (real / "libfixture.so.1").symlink_to("libfixture-1.so")
    evidence = {
        "size": len(data),
        "sha256": hashlib.sha256(data).hexdigest(),
        "abi": elf_identity(data),
        "platform": "linux-x86_64",
        "provenance": "fixture",
    }
    for key, path, target in (
        ("provider", alias / "libfixture-1.so", None),
        ("mirror", real / "libfixture-1.so", None),
        ("alias", alias / "libfixture.so.1", "provider"),
        ("mirror-alias", real / "libfixture.so.1", "mirror"),
    ):
        config["host_files"][key] = str(path)
        value["host_files"][key] = dict(
            evidence, **({"alias_of": target} if target else {})
        )
    row = {
        "role": "host",
        "target_role": "host",
        "path": str(alias)[1:],
        "target_path": str(real)[1:],
    }
    value["directory_aliases"] = [row]
    consumer = Path(config["roots"]["java"]) / "java"
    content = elf(needed=("libfixture.so.1",), runpath=str(real))
    consumer.write_bytes(content)
    entry = next(e for e in value["entries"] if e["role"] == "java")
    entry.update(
        size=len(content),
        sha256=hashlib.sha256(content).hexdigest(),
        abi=elf_identity(content),
    )
    if mode == "absent":
        value["directory_aliases"] = []
    elif mode == "missing-mirror-alias":
        del config["host_files"]["mirror-alias"]
        del value["host_files"]["mirror-alias"]
    elif mode == "wrong-hop":
        value["host_files"]["mirror-alias"]["alias_of"] = "provider"
    elif mode == "wrong-hash":
        value["host_files"]["mirror"]["sha256"] = "0" * 64
    elif mode == "wrong-abi":
        value["host_files"]["mirror"]["abi"] = elf_identity(elf())
    elif mode == "copy":
        value["directory_aliases"] = []
        alias.unlink()
        alias.mkdir()
        (alias / "libfixture-1.so").write_bytes(data)
        (alias / "libfixture.so.1").symlink_to("libfixture-1.so")
    elif mode == "undeclared-source":
        for key in ("provider", "alias"):
            del config["host_files"][key]
            del value["host_files"][key]
    elif mode == "undeclared-target":
        for key in ("mirror", "mirror-alias"):
            del config["host_files"][key]
            del value["host_files"][key]
    elif mode == "false-target":
        false_target = root / "false-target"
        false_target.mkdir()
        (false_target / "evidence.so").write_bytes(data)
        row["target_path"] = str(false_target)[1:]
        config["host_files"]["false-evidence"] = str(false_target / "evidence.so")
        value["host_files"]["false-evidence"] = evidence.copy()
    elif mode == "nonlink":
        row["path"] = str(real)[1:]
    elif mode == "private-overlap":
        row["path"] = str(Path(config["roots"]["java"]) / "link")[1:]
        private_alias = Path("/" + row["path"])
        private_alias.symlink_to(real, target_is_directory=True)
        config["host_files"]["provider"] = str(private_alias / "libfixture-1.so")
        config["host_files"]["alias"] = str(private_alias / "libfixture.so.1")
    raw = canonical(value)
    Path(config["roots"]["tool"], "runtime/portrait/inventory.json").write_bytes(raw)
    config["inventory_sha256"] = hashlib.sha256(raw).hexdigest()
    write_descriptor(config, value)
    return config


@unittest.skipUnless(os.name == "posix", "requires actual directory symlinks")
class HostDirectoryAliasTests(unittest.TestCase):
    def test_complete_resolver_accepts_witnessed_mirrored_file_chains(self):
        with tempfile.TemporaryDirectory() as tmp:
            _resolve_bundle(host_fixture(Path(tmp)))

    def test_unproven_host_inputs_rejected(self):
        for mode in (
            "absent",
            "missing-mirror-alias",
            "wrong-hop",
            "wrong-hash",
            "wrong-abi",
            "copy",
            "undeclared-source",
            "undeclared-target",
            "false-target",
            "nonlink",
            "private-overlap",
        ):
            with (
                self.subTest(mode=mode),
                tempfile.TemporaryDirectory() as tmp,
                self.assertRaisesRegex(
                    BundleError,
                    {
                        "private-overlap": "host directory alias overlaps private root",
                        "false-target": "directory alias target mismatch",
                    }.get(mode, ".*"),
                ),
            ):
                _resolve_bundle(host_fixture(Path(tmp), mode))

    def test_host_rows_require_explicit_context_and_bounded_paths(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            target = root / "target"
            target.mkdir()
            alias = root / "alias"
            alias.symlink_to(target, target_is_directory=True)
            row = {
                "role": "host",
                "target_role": "host",
                "path": str(alias)[1:],
                "target_path": str(target)[1:],
            }
            with self.assertRaisesRegex(BundleError, "host context"):
                directory_aliases({}, [row])
            host_files = {"a": str(alias / "a"), "b": str(target / "b")}
            for bad in ("/absolute", "../escape", "", "a/../b"):
                with self.subTest(path=bad), self.assertRaises(BundleError):
                    directory_aliases({}, [dict(row, path=bad)], host_files=host_files)
            with self.assertRaisesRegex(BundleError, "bound"):
                directory_aliases({}, [row] * 129, host_files=host_files)
            alias.unlink()
            target.rmdir()
            alias.symlink_to(target, target_is_directory=True)
            target.symlink_to(alias, target_is_directory=True)
            reverse = dict(row, path=row["target_path"], target_path=row["path"])
            with self.assertRaisesRegex(BundleError, "cycle"):
                directory_aliases({}, [row, reverse], host_files=host_files)

    def test_nonmapping_host_context_is_typed_bundle_error(self):
        for bad in (["/secret/path"], "secret-context"):
            with self.subTest(context=bad), tempfile.TemporaryDirectory() as tmp:
                config = host_fixture(Path(tmp))
                config["host_files"] = bad
                with self.assertRaisesRegex(BundleError, "host context"):
                    _resolve_bundle(config)

    def test_nonmapping_host_context_cli_returns_unready_json(self):
        for bad in (["/secret/path"], "secret-context"):
            with self.subTest(context=bad), tempfile.TemporaryDirectory() as tmp:
                root = Path(tmp)
                config = host_fixture(root)
                config["host_files"] = bad
                path = root / "config.json"
                path.write_bytes(canonical(config))
                with contextlib.redirect_stdout(io.StringIO()) as output:
                    status = bundle.main(["--config", str(path)])
                self.assertEqual(status, 2)
                self.assertEqual(
                    json.loads(output.getvalue()),
                    {
                        "enabled": False,
                        "configuration_verified": False,
                        "execution_admitted": False,
                        "error": "bundle_unready",
                    },
                )
