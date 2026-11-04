"""Dynamic selection and actual runner-boundary tests; subprocesses are fakes."""

import hashlib
import json
import os
import tempfile
import time
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from app.platform.portrait_dynamic_profile import verify_profile
from app.platform.portrait_linux_runtime import ManagedLaunch
from server_app.portraits.bundle import BundleError, _resolve_bundle
from test_portrait_bundle_review_unittest import seal
from test_portrait_bundle_unittest import fixture


class DynamicProfileTests(unittest.TestCase):
    def test_dynamic_vendor_cannot_be_shadowed_by_unlisted_host_loader_file(self):
        from test_portrait_bundle_unittest import elf

        with tempfile.TemporaryDirectory() as temp:
            config, value = fixture(Path(temp))
            value["descriptor_groups"]["x_libraries"].insert(0, ["host", "unshare"])
            config["loader_dirs"] = value["loader_dirs"] = [
                ["host", "unshare"],
                ["native", "dri"],
                ["native", ""],
            ]
            seal(config, value)
            _resolve_bundle(config)
            host = Path(config["host_files"]["unshare"]).parent
            (host / "libGLX_mesa.so.0").write_bytes(elf() + b"unlisted dynamic shadow")
            with self.assertRaisesRegex(BundleError, "dynamic.*loader"):
                _resolve_bundle(config)

    def test_builtin_fonts_bind_actual_xvfb_and_needed_provider(self):
        from app.platform.portrait_dynamic_profile import guarded_runtime
        from server_app.portraits.bundle import elf_identity
        from test_portrait_bundle_review_unittest import add
        from test_portrait_bundle_unittest import elf

        with tempfile.TemporaryDirectory() as temp:
            config, value = fixture(Path(temp))
            value["dynamic_profile"]["font_mode"] = "built-ins"
            value["dynamic_profile"].pop("font_tree")
            provider = add(
                config,
                value,
                "native",
                "libXfont2.so.2",
                elf(soname="libXfont2.so.2") + b"built-ins\0",
                "elf",
            )
            value["descriptor_groups"]["x_libraries"].append(provider)
            value["descriptor_groups"]["x_libraries"].sort()
            raw = (
                elf(needed=("libXfont2.so.2",))
                + b"/usr/share/X11/xkb\0/usr/bin/xkbcomp\0built-ins\0"
            )
            path = Path(config["roots"]["display"], "Xvfb")
            path.write_bytes(raw)
            entry = next(
                e
                for e in value["entries"]
                if e["role"] == "display" and e["path"] == "Xvfb"
            )
            entry.update(
                size=len(raw),
                sha256=hashlib.sha256(raw).hexdigest(),
                abi=elf_identity(raw),
            )
            seal(config, value)
            bundle = _resolve_bundle(config)
            runtime = guarded_runtime(
                json.loads(Path(config["descriptor_path"]).read_bytes())
            )
            self.assertEqual(runtime.dynamic_profile.font_argument, "built-ins")
            self._exercise_display(runtime, Path(temp))
            self.assertTrue(bundle.tool_fingerprint)
            value["dynamic_profile"]["font_mode"] = "directory"
            value["dynamic_profile"]["font_tree"] = ["display", "share/fonts/X11/misc"]
            seal(config, value)
            self.assertNotEqual(
                bundle.tool_fingerprint, _resolve_bundle(config).tool_fingerprint
            )
            value["dynamic_profile"]["font_mode"] = "built-ins"
            value["dynamic_profile"].pop("font_tree")
            provider_path = Path(config["roots"]["native"], "libXfont2.so.2")
            raw = elf(soname="libXfont2.so.2")
            provider_path.write_bytes(raw)
            entry = next(
                e
                for e in value["entries"]
                if e["role"] == "native" and e["path"] == "libXfont2.so.2"
            )
            entry.update(
                size=len(raw),
                sha256=hashlib.sha256(raw).hexdigest(),
                abi=elf_identity(raw),
            )
            seal(config, value)
            with self.assertRaises(BundleError):
                _resolve_bundle(config)

    def test_mount_namespace_owner_must_match_mapped_user_namespace(self):
        from server_app.portraits import private_namespace as ns
        from server_app.portraits.executor import BoundaryError

        fake_fcntl = SimpleNamespace(ioctl=lambda fd, request: 41)
        for owner in (2, 3):
            with (
                self.subTest(owner=owner),
                patch.dict("sys.modules", {"fcntl": fake_fcntl}),
                patch.object(Path, "read_text", return_value="0 1000 1"),
                patch.object(ns.os, "open", return_value=42),
                patch.object(
                    ns.os, "fstat", return_value=SimpleNamespace(st_dev=1, st_ino=owner)
                ),
                patch.object(
                    ns.os, "stat", return_value=SimpleNamespace(st_dev=1, st_ino=2)
                ),
                patch.object(ns.os, "close") as close,
            ):
                if owner == 2:
                    ns._require_private_namespace({"caller_uid": 1000})
                else:
                    with self.assertRaises(BoundaryError):
                        ns._require_private_namespace({"caller_uid": 1000})
                self.assertEqual(
                    [call.args[0] for call in close.call_args_list], [41, 42]
                )

    def test_compact_profile_cannot_contain_mutable_nested_values(self):
        from app.platform.portrait_dynamic_profile import SCHEMA, DynamicProfile

        with self.assertRaises(ValueError):
            DynamicProfile(SCHEMA, [], "/glx", "/egl", "/json", "/share", "/font")

    def test_standard_xvfb_split_compiler_path_is_supported(self):
        from server_app.portraits.bundle import elf_identity
        from test_portrait_bundle_unittest import elf

        with tempfile.TemporaryDirectory() as temp:
            config, value = fixture(Path(temp))
            raw = elf() + b'/usr/share/X11/xkb\0/usr/bin\0"%s%sxkbcomp" -w %d %s\0'
            path = Path(config["roots"]["display"], "Xvfb")
            path.write_bytes(raw)
            entry = next(
                e
                for e in value["entries"]
                if e["role"] == "display" and e["path"] == "Xvfb"
            )
            entry.update(
                size=len(raw),
                sha256=hashlib.sha256(raw).hexdigest(),
                abi=elf_identity(raw),
            )
            seal(config, value)
            self.assertTrue(_resolve_bundle(config).tool_fingerprint)

    def test_resource_alias_hops_are_manifest_bound(self):
        from test_portrait_bundle_unittest import canonical

        for declared in (False, True):
            with self.subTest(declared=declared), tempfile.TemporaryDirectory() as temp:
                config, value = fixture(Path(temp))
                share = Path(config["roots"]["display"], "share")
                alias, target = (
                    share / "X11/xkb/symbol_alias",
                    share / "X11/xkb/symbols",
                )
                alias.mkdir()
                if declared:
                    value["directory_aliases"].append(
                        {
                            "role": "display",
                            "path": "share/X11/xkb/symbol_alias",
                            "target_role": "display",
                            "target_path": "share/X11/xkb/symbols",
                        }
                    )
                    manifest_path = Path(
                        config["roots"]["display"], "tree-manifest.json"
                    )
                    manifest = json.loads(manifest_path.read_bytes())
                    manifest["aliases"] = [
                        {"path": "X11/xkb/symbol_alias", "target": "X11/xkb/symbols"}
                    ]
                    raw = canonical(manifest)
                    manifest_path.write_bytes(raw)
                    entry = next(
                        e
                        for e in value["entries"]
                        if e["role"] == "display" and e["path"] == "tree-manifest.json"
                    )
                    entry.update(size=len(raw), sha256=hashlib.sha256(raw).hexdigest())
                seal(config, value)
                original_link, original_readlink = Path.is_symlink, Path.readlink
                with (
                    patch.object(
                        Path,
                        "is_symlink",
                        lambda path, alias=alias, original_link=original_link: (
                            path == alias or original_link(path)
                        ),
                    ),
                    patch.object(
                        Path,
                        "readlink",
                        lambda path, alias=alias, original_readlink=original_readlink: (
                            Path("symbols")
                            if path == alias
                            else original_readlink(path)
                        ),
                    ),
                ):
                    if declared:
                        self.assertTrue(_resolve_bundle(config).tool_fingerprint)
                    else:
                        with self.assertRaises(BundleError):
                            _resolve_bundle(config)
                self.assertTrue(target.is_dir())

    def test_managed_prepare_rejects_copied_elf_before_staging(self):
        from dataclasses import replace

        from app.platform.portrait_dynamic_profile import guarded_runtime
        from app.platform.portrait_linux_runtime import RuntimeFile

        with tempfile.TemporaryDirectory() as temp:
            config, _ = fixture(Path(temp) / "inputs")
            runtime = guarded_runtime(
                json.loads(Path(config["descriptor_path"]).read_bytes())
            )
            elf = runtime.x_libraries[0]
            runtime = replace(
                runtime,
                app_files=(*runtime.app_files, RuntimeFile(elf.path, elf.sha256)),
            )
            work = Path(temp) / "work"
            work.mkdir()
            with self.assertRaisesRegex(ValueError, "staged app ELF"):
                runtime.prepare(work, deadline=time.monotonic() + 10)
            self.assertEqual(list(work.iterdir()), [])

    def test_namespace_binds_only_fixed_existing_targets_readonly_without_mtab(self):
        from server_app.portraits import private_namespace as ns

        with tempfile.TemporaryDirectory() as temp:
            config, _ = fixture(Path(temp) / "inputs")
            native = json.loads(Path(config["descriptor_path"]).read_bytes())
            attempt = Path(temp) / "attempt"
            attempt.mkdir()
            python = Path(config["roots"]["python"], "python")
            sha = hashlib.sha256(python.read_bytes()).hexdigest()
            value = {
                "caller_uid": 1000,
                "namespace_tools": {
                    "/usr/bin/mount": "a" * 64,
                    "/usr/bin/sh": "a" * 64,
                },
                "python": str(python),
                "python_sha256": sha,
            }
            original_is_dir = Path.is_dir

            def is_dir(path):
                return str(path) in (
                    "/etc",
                    "/usr/share",
                    "\\etc",
                    "\\usr\\share",
                ) or original_is_dir(path)

            def digest(path):
                path = Path(path)
                if path in {Path(name) for name in value["namespace_tools"]}:
                    return "a" * 64
                return (
                    hashlib.sha256(path.read_bytes()).hexdigest()
                    if path.is_file()
                    else "a" * 64
                )

            with (
                patch.object(ns, "_require_private_namespace") as namespace_check,
                patch.object(ns, "_sha", side_effect=digest),
                patch.object(ns, "copy_namespace_tools"),
                patch.object(Path, "symlink_to"),
                patch.object(Path, "is_dir", is_dir),
                patch(
                    "app.platform.resource_lease.inherited_lease_kwargs",
                    return_value={},
                ),
                patch.object(ns.subprocess, "run") as mount,
            ):
                ns.prepare_namespace(
                    value, native, attempt, lambda: None, time.monotonic() + 10
                )
            namespace_check.assert_called_once_with(value)
            commands = [call.args[0] for call in mount.call_args_list]
            self.assertTrue(
                all(command[:2] == ["/usr/bin/mount", "-n"] for command in commands)
            )
            for target in ("/usr/share", "/etc"):
                self.assertIn(
                    [
                        "/usr/bin/mount",
                        "-n",
                        "-o",
                        "remount,bind,ro,nodev,nosuid,noexec",
                        target,
                    ],
                    commands,
                )
            self.assertIn(
                [
                    "/usr/bin/mount",
                    "-n",
                    "--bind",
                    native["dynamic_profile"]["share_tree"],
                    "/usr/share",
                ],
                commands,
            )
            self.assertEqual(list((attempt / "empty-etc").iterdir()), [])

    def test_namespace_refusal_precedes_any_mount(self):
        from server_app.portraits import private_namespace as ns
        from server_app.portraits.executor import BoundaryError

        with (
            patch.object(
                ns,
                "_require_private_namespace",
                side_effect=BoundaryError("not private"),
            ),
            patch.object(ns.subprocess, "run") as run,
        ):
            with self.assertRaises(BoundaryError):
                ns.prepare_namespace(
                    {}, {}, Path("unused"), lambda: None, time.monotonic() + 10
                )
            run.assert_not_called()

    def test_real_display_call_uses_pinned_font_directory(self):
        from app.platform.portrait_dynamic_profile import guarded_runtime

        with tempfile.TemporaryDirectory() as temp:
            config, _ = fixture(Path(temp) / "inputs")
            runtime = guarded_runtime(
                json.loads(Path(config["descriptor_path"]).read_bytes())
            )
            self._exercise_display(runtime, Path(temp))

    def _exercise_display(self, runtime, root):
        import app.platform.portrait_linux_runtime as runtime_module

        app = root / "attempt" / "managed-app"
        app.mkdir(parents=True)
        launch = ManagedLaunch(runtime, app, time.monotonic() + 10, None)
        process = MagicMock()
        process.poll.return_value = None
        thread = MagicMock()
        thread.is_alive.return_value = False
        with (
            patch.dict(
                "sys.modules",
                {
                    "resource": SimpleNamespace(
                        RLIMIT_CORE=4, getrlimit=lambda _: (0, 0)
                    )
                },
            ),
            patch.object(
                runtime_module,
                "os",
                SimpleNamespace(name="posix", pipe=os.pipe, close=os.close),
            ),
            patch.object(
                runtime_module,
                "inherited_lease_kwargs",
                return_value={"pass_fds": (99,)},
            ),
            patch.object(runtime_module.RuntimeFile, "verify"),
            patch.object(runtime_module, "_StreamCapture"),
            patch.object(runtime_module.threading, "Thread", return_value=thread),
            patch.object(
                runtime_module.PrivateDisplay,
                "_read_display_number",
                return_value="7",
            ),
            patch.object(
                runtime_module.subprocess, "Popen", return_value=process
            ) as popen,
            runtime_module.PrivateDisplay(launch),
        ):
            args = popen.call_args.args[0]
            self.assertEqual(
                args[args.index("-fp") + 1], runtime.dynamic_profile.font_argument
            )
            self.assertEqual(launch.env["DISPLAY"], ":7")
        self.assertIsNone(launch.display)

    def test_compact_profile_is_frozen_and_descriptor_has_no_tree_array(self):
        from dataclasses import FrozenInstanceError

        from app.platform.portrait_dynamic_profile import guarded_runtime

        with tempfile.TemporaryDirectory() as temp:
            config, _ = fixture(Path(temp))
            native = json.loads(Path(config["descriptor_path"]).read_bytes())
            self.assertNotIn("files", native["dynamic_profile"])
            runtime = guarded_runtime(native)
            self.assertFalse(hasattr(runtime.dynamic_profile, "files"))
            with self.assertRaises(FrozenInstanceError):
                runtime.dynamic_profile.share_tree = "/ambient"

    def test_actual_managed_environment_overrides_ambient_dynamic_inputs(self):
        with tempfile.TemporaryDirectory() as temp:
            config, _ = fixture(Path(temp))
            native = json.loads(Path(config["descriptor_path"]).read_bytes())
            profile = verify_profile(native["dynamic_profile"])
            app = Path(temp) / "work" / "managed-app"
            app.mkdir(parents=True)
            runtime = SimpleNamespace(
                dotnet=SimpleNamespace(path=native["dotnet"]["path"]),
                native_files=(),
                x_libraries=(),
                dynamic_profile=profile,
            )
            with patch.dict(
                os.environ,
                {
                    "LIBGL_DRIVERS_PATH": "/ambient",
                    "MESA_SHADER_CACHE_DIR": "/ambient",
                    "__GLX_VENDOR_LIBRARY_NAME": "ambient",
                    "DOTNET_SYSTEM_GLOBALIZATION_INVARIANT": "0",
                    "FONTCONFIG_FILE": "/ambient",
                    "XKB_CONFIG_ROOT": "/ambient",
                    "HOME": "/ambient",
                    "XDG_CONFIG_HOME": "/ambient",
                    "LD_PRELOAD": "/ambient",
                },
            ):
                launch = ManagedLaunch(runtime, app, time.monotonic() + 10, None)
            self.assertEqual(
                launch.env.get("LIBGL_DRIVERS_PATH"),
                str(Path(profile.dri_driver).parent),
            )
            self.assertEqual(
                launch.env.get("DOTNET_SYSTEM_GLOBALIZATION_INVARIANT"), "1"
            )
            self.assertEqual(launch.env.get("__GLX_VENDOR_LIBRARY_NAME"), "mesa")
            self.assertEqual(launch.env.get("MESA_SHADER_CACHE_DISABLE"), "true")
            self.assertFalse(
                {
                    "MESA_SHADER_CACHE_DIR",
                    "FONTCONFIG_FILE",
                    "XKB_CONFIG_ROOT",
                    "LD_PRELOAD",
                }
                & launch.env.keys()
            )
            for key in ("HOME", "XDG_CONFIG_HOME", "XDG_CACHE_HOME"):
                self.assertTrue(Path(launch.env[key]).is_relative_to(app.parent))
                self.assertEqual(list(Path(launch.env[key]).iterdir()), [])

    def test_resource_bytes_and_selection_are_bound_and_relocation_stable(self):
        with tempfile.TemporaryDirectory() as temp:
            a, _ = fixture(Path(temp) / "a")
            b, _ = fixture(Path(temp) / "b")
            self.assertEqual(
                _resolve_bundle(a).tool_fingerprint, _resolve_bundle(b).tool_fingerprint
            )
            path = Path(a["roots"]["display"], "share/X11/xkb/symbols/pc")
            path.write_bytes(b"unbound change")
            with self.assertRaises(BundleError):
                _resolve_bundle(a)

    def test_omitted_profile_unknown_selection_and_ambient_vendor_fail(self):
        for kind in ("missing", "unknown", "vendor", "icu"):
            with self.subTest(kind=kind), tempfile.TemporaryDirectory() as temp:
                config, value = fixture(Path(temp))
                if kind == "missing":
                    value.pop("dynamic_profile")
                elif kind == "unknown":
                    value["dynamic_profile"]["schema"] = "unknown"
                    # Write without the fixture's binding helper: invalid wire is intentional.
                    raw = json.dumps(
                        value, sort_keys=True, separators=(",", ":")
                    ).encode()
                    Path(
                        config["roots"]["tool"], "runtime/portrait/inventory.json"
                    ).write_bytes(raw)
                    config["inventory_sha256"] = hashlib.sha256(raw).hexdigest()
                else:
                    name = (
                        "egl-vendor.json"
                        if kind == "vendor"
                        else "runtime/portrait/managed/SpineViewerCLI.runtimeconfig.json"
                    )
                    role = "display" if kind == "vendor" else "tool"
                    path = Path(config["roots"][role], name)
                    doc = json.loads(path.read_bytes())
                    if kind == "vendor":
                        doc["ICD"]["library_path"] = "/ambient/libEGL.so"
                    else:
                        doc["runtimeOptions"]["configProperties"][
                            "System.Globalization.Invariant"
                        ] = False
                    raw = json.dumps(
                        doc, sort_keys=True, separators=(",", ":")
                    ).encode()
                    path.write_bytes(raw)
                    entry = next(
                        e
                        for e in value["entries"]
                        if e["role"] == role and e["path"] == name
                    )
                    entry.update(size=len(raw), sha256=hashlib.sha256(raw).hexdigest())
                if kind != "unknown":
                    seal(config, value)
                with self.assertRaises(BundleError):
                    _resolve_bundle(config)

    def test_guarded_descriptor_missing_profile_rejected(self):
        from app.platform.portrait_dynamic_profile import descriptor_files

        with self.assertRaisesRegex(ValueError, "dynamic profile"):
            list(descriptor_files({}))
