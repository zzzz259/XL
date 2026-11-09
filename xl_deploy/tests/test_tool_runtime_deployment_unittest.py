"""Sparse release membership and planner mapping for the updater Tool runtime.

The updater release ships the Tool sources needed by the headless portrait
pipeline (see Task 1/2). These tests pin both directions: the runtime files
must stage into a release and import headlessly there, while GUI sources,
docs and tests must stay out of releases and out of deployment triggers.
"""
import ast
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

from xl_deploy import config
from xl_deploy.planner import plan_deployment
from xl_deploy.runner import (
    SPARSE_CHECKOUT_PATTERNS,
    TOOL_RELEASE_RULES,
    CommandRunner,
    is_tool_runtime_path,
)

ROOT = Path(__file__).resolve().parents[2]
TOOL = ROOT / "xl_updata_tool"

BACKEND_TEST = ("xl-updata-server-test.service",)
BACKEND_MAIN = ("xl-updata-server.service",)


def git(repo, *args):
    return subprocess.run(
        ["git", "-c", "user.name=Tool Runtime Test", "-c",
         "user.email=tool-runtime@example.invalid", *map(str, args)],
        cwd=repo, check=True, capture_output=True, text=True).stdout.strip()


def _touch(repo, relative, content="fixture"):
    path = repo / relative
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")
    return path


# Synthetic stand-ins at the exact tracked paths of the bundled native tools;
# the real binaries are hundreds of megabytes of GUI-only desktop publish.
SPINE_VIEWER_TRACKED = (
    "xl_updata_tool/tools/SpineViewer/PortraitSceneCLI.dll",
    "xl_updata_tool/tools/SpineViewer/PortraitSceneCLI.runtimeconfig.json",
    "xl_updata_tool/tools/SpineViewer/PortraitSceneCLI.deps.json",
    "xl_updata_tool/tools/SpineViewer/Spine.dll",
    "xl_updata_tool/tools/SpineViewer/SpineRuntime21.dll",
    "xl_updata_tool/tools/SpineViewer/SpineRuntime34.dll",
    "xl_updata_tool/tools/SpineViewer/SpineRuntime35.dll",
    "xl_updata_tool/tools/SpineViewer/SpineRuntime36.dll",
    "xl_updata_tool/tools/SpineViewer/SpineRuntime37.dll",
    "xl_updata_tool/tools/SpineViewer/SpineRuntime38.dll",
    "xl_updata_tool/tools/SpineViewer/SpineRuntime40.dll",
    "xl_updata_tool/tools/SpineViewer/SpineRuntime41.dll",
    "xl_updata_tool/tools/SpineViewer/SpineRuntime42.dll",
    "xl_updata_tool/tools/SpineViewer/SFML.Graphics.dll",
    "xl_updata_tool/tools/SpineViewer/SFML.System.dll",
    "xl_updata_tool/tools/SpineViewer/SFML.Window.dll",
    "xl_updata_tool/tools/SpineViewer/SkiaSharp.dll",
    "xl_updata_tool/tools/SpineViewer/System.Text.Json.dll",
    "xl_updata_tool/tools/SpineViewer/System.Text.Encodings.Web.dll",
    "xl_updata_tool/tools/SpineViewer/System.IO.Pipelines.dll",
    "xl_updata_tool/tools/SpineViewer/Spectre.Console.dll",
    "xl_updata_tool/tools/SpineViewer/System.CommandLine.dll",
    "xl_updata_tool/tools/SpineViewer/FFMpegCore.dll",
    "xl_updata_tool/tools/SpineViewer/Instances.dll",
    "xl_updata_tool/tools/SpineViewer/PsdWriter.dll",
    "xl_updata_tool/tools/SpineViewer/libSkiaSharp.dll",
    "xl_updata_tool/tools/SpineViewer/NLog.dll",
    "xl_updata_tool/tools/SpineViewer/SpineViewer.exe",
    "xl_updata_tool/tools/SpineViewer/SpineViewer.dll",
    "xl_updata_tool/tools/SpineViewer/SpineViewerCLI.exe",
    "xl_updata_tool/tools/SpineViewer/PresentationFramework.dll",
    "xl_updata_tool/tools/SpineViewer/ffmpeg.exe",
    "xl_updata_tool/tools/SpineViewer/runtimes/linux-x64/native/libcsfml-graphics.so",
    "xl_updata_tool/tools/SpineViewer/runtimes/linux-x64/native/libcsfml-graphics.so.2.6",
    "xl_updata_tool/tools/SpineViewer/runtimes/linux-x64/native/libsfml-window.so.2.6",
    "xl_updata_tool/tools/SpineViewer/runtimes/linux-x64/native/libSkiaSharp.so",
    "xl_updata_tool/tools/SpineViewer/linux/SpineViewerCLI.dll",
    "xl_updata_tool/tools/SpineViewer/linux/SpineViewerCLI.runtimeconfig.json",
    "xl_updata_tool/tools/SpineViewer/linux/SpineViewerCLI.deps.json",
    "xl_updata_tool/tools/SpineViewer/linux/Spine.dll",
    "xl_updata_tool/tools/SpineViewer/linux/SpineRuntime42.dll",
    "xl_updata_tool/tools/SpineViewer/linux/SFML.Graphics.dll",
    "xl_updata_tool/tools/SpineViewer/linux/SFML.System.dll",
    "xl_updata_tool/tools/SpineViewer/linux/SFML.Window.dll",
    "xl_updata_tool/tools/SpineViewer/linux/SkiaSharp.dll",
    "xl_updata_tool/tools/SpineViewer/linux/Spectre.Console.dll",
    "xl_updata_tool/tools/SpineViewer/linux/System.CommandLine.dll",
    "xl_updata_tool/tools/SpineViewer/linux/NLog.dll",
    "xl_updata_tool/tools/SpineViewer/linux/FFMpegCore.dll",
    "xl_updata_tool/tools/SpineViewer/linux/Instances.dll",
    "xl_updata_tool/tools/SpineViewer/linux/PsdWriter.dll",
    "xl_updata_tool/tools/SpineViewer/linux/System.Text.Json.dll",
    "xl_updata_tool/tools/SpineViewer/linux/System.Text.Encodings.Web.dll",
    "xl_updata_tool/tools/SpineViewer/linux/System.IO.Pipelines.dll",
    "xl_updata_tool/tools/SpineViewer/linux/runtimes/linux-x64/native/libSkiaSharp.so",
    "xl_updata_tool/tools/SpineViewer/linux/runtimes/linux-x64/native/libcsfml-graphics.so",
    "xl_updata_tool/tools/SpineViewer/runtimes/win-x64/native/libSkiaSharp.dll",
    "xl_updata_tool/tools/SpineViewer/runtimes/osx-x64/native/libSkiaSharp.dylib",
)

ASSET_STUDIO_TRACKED = (
    "xl_updata_tool/tools/AssetStudio/AssetStudio.CLI.dll",
    "xl_updata_tool/tools/AssetStudio/AssetStudio.CLI.deps.json",
    "xl_updata_tool/tools/AssetStudio/AssetStudio.CLI.dll.config",
    "xl_updata_tool/tools/AssetStudio/AssetStudio.CLI.runtimeconfig.json",
    "xl_updata_tool/tools/AssetStudio/AssetStudio.dll",
    "xl_updata_tool/tools/AssetStudio/AssetStudio.Utility.dll",
    "xl_updata_tool/tools/AssetStudio/Keys.json",
    "xl_updata_tool/tools/AssetStudio/AI/versions.json",
    "xl_updata_tool/tools/AssetStudio/AssetStudio.GUI.dll",
    "xl_updata_tool/tools/AssetStudio/AssetStudio.GUI.deps.json",
    "xl_updata_tool/tools/AssetStudio/AssetStudio.GUI.exe",
    "xl_updata_tool/tools/AssetStudio/AssetStudio.CLI.exe",
    "xl_updata_tool/tools/AssetStudio/AssetStudio.CLI.pdb",
    "xl_updata_tool/tools/AssetStudio/log.txt",
    "xl_updata_tool/tools/AssetStudio/x64/AssetStudio.FBXNative.dll",
    "xl_updata_tool/tools/AssetStudio/x86/AssetStudio.FBXNative.dll",
    "xl_updata_tool/tools/AssetStudio/zh-Hans/System.CommandLine.resources.dll",
    "xl_updata_tool/tools/AssetStudio/runtimes/linux-x64/native/libglfw.so.3.3",
    "xl_updata_tool/tools/AssetStudio/runtimes/win-x64/native/e_sqlite3.dll",
)


class StagedToolReleaseTests(unittest.TestCase):
    """Stage a disposable repo with the real Tool app tree plus synthetic tool
    binaries; assert exact release membership and headless importability."""

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.repo = Path(self.temp.name) / "source"
        self.repo.mkdir()
        git(self.repo, "init")
        (self.repo / ".gitignore").write_text(
            "*.db\nlogs/\n__pycache__/\n", encoding="utf-8")
        _touch(self.repo, "xl_deploy/a.py")
        _touch(self.repo, "xl_updata_server/a.py")
        _touch(self.repo, "xl_qqbot/bot_app/__init__.py", "")
        _touch(self.repo, "README.md")
        # Real Python sources keep the import closure assertions honest.
        shutil.copytree(TOOL / "app", self.repo / "xl_updata_tool" / "app",
                        ignore=shutil.ignore_patterns("__pycache__", "*.pyc"))
        shutil.copytree(TOOL / "native", self.repo / "xl_updata_tool" / "native")
        scripts = self.repo / "xl_updata_tool" / "scripts"
        scripts.mkdir(parents=True)
        shutil.copy2(TOOL / "scripts" / "build-portrait-scene-renderer.py", scripts)
        for relative in (*SPINE_VIEWER_TRACKED, *ASSET_STUDIO_TRACKED,
                         "xl_updata_tool/tools/lua/unluac.jar",
                         "xl_updata_tool/tools/lua/opmap",
                         "xl_updata_tool/README.md",
                         "xl_updata_tool/requirements.txt",
                         "xl_updata_tool/tests/test_preview.py"):
            _touch(self.repo, relative)
        git(self.repo, "add", ".")
        git(self.repo, "commit", "-m", "fixture")
        self.sha = git(self.repo, "rev-parse", "HEAD")
        self.release = Path(self.temp.name) / "releases" / self.sha
        CommandRunner(repository=self.repo).stage(
            SimpleNamespace(sha=self.sha, branch="test"), self.release)
        self.tool = self.release / "xl_updata_tool"

    def test_sparse_argv_comes_from_the_shared_release_rules(self):
        rebuilt = tuple(
            ("!/" + path if not include else "/" + path)
            for include, path in TOOL_RELEASE_RULES
        )
        self.assertEqual(SPARSE_CHECKOUT_PATTERNS, rebuilt)
        self.assertIn("/xl_updata_tool/app/platform/", SPARSE_CHECKOUT_PATTERNS)
        self.assertIn(
            "!/xl_updata_tool/app/features/preview/portrait_config/import_service.py",
            SPARSE_CHECKOUT_PATTERNS)

    def test_headless_entry_imports_inside_release(self):
        result = subprocess.run(
            [sys.executable, "-I", "-c",
             ("import sys; sys.path.insert(0, sys.argv[1]); "
              "import app.features.preview.portrait_headless; "
              "import app.features.preview.portrait_config.lua_config; "
              "import app.platform.portrait_render_process; "
              "import app.features.characters.parser"),
             str(self.tool)],
            cwd=self.release, text=True, capture_output=True, check=False)
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_every_app_import_resolves_inside_release(self):
        """Static closure guard: no staged app import may dangle, even lazy ones."""

        def resolves(name):
            relative = Path(*name.split("."))
            return ((self.tool / relative.with_suffix(".py")).is_file()
                    or (self.tool / relative / "__init__.py").is_file())

        app_root = self.tool / "app"
        for source in app_root.rglob("*.py"):
            tree = ast.parse(source.read_text(encoding="utf-8"))
            package = source.parent.relative_to(self.tool)
            module = ".".join(package.parts + (() if source.name == "__init__.py"
                                               else (source.stem,)))
            for node in ast.walk(tree):
                imported = []
                if isinstance(node, ast.Import):
                    imported = [alias.name for alias in node.names]
                elif isinstance(node, ast.ImportFrom):
                    base = node.module or ""
                    if node.level:
                        pkg = module if source.name == "__init__.py" else module.rpartition(".")[0]
                        for _ in range(node.level - 1):
                            pkg = pkg.rpartition(".")[0]
                        base = f"{pkg}.{base}".rstrip(".")
                    imported = [base]
                    if not node.module:
                        # ``from <package> import <submodule>``: pin each name too.
                        imported.extend(f"{base}.{alias.name}" for alias in node.names
                                        if alias.name != "*")
                for name in imported:
                    if name.split(".")[0] != "app":
                        continue
                    self.assertTrue(
                        resolves(name),
                        f"{source.relative_to(self.tool)} imports missing {name}")

    def test_desktop_gui_sources_stay_out_of_release(self):
        for relative in (
            "app/features/preview/controller.py",
            "app/features/preview/service.py",
            "app/features/preview/fgui.py",
            "app/features/preview/fgui_atlas.py",
            "app/features/preview/dialogs/__init__.py",
            "app/features/preview/workers/__init__.py",
            "app/features/preview/worker.py",
            "app/features/preview/page.py",
            "app/features/preview/item.py",
            "app/features/preview/adapter.py",
            "app/features/preview/export_controller.py",
            "app/features/preview/catalog.py",
            "app/features/characters/page.py",
            "app/features/preview/portrait_config/import_service.py",
        ):
            with self.subTest(relative=relative):
                self.assertFalse((self.tool / relative).exists(), relative)

    def test_native_renderer_and_build_script_ship(self):
        self.assertTrue((self.tool / "native/portrait_scene/Program.cs").is_file())
        self.assertTrue((self.tool / "native/portrait_scene/FramePipeline.cs").is_file())
        self.assertTrue(
            (self.tool / "scripts/build-portrait-scene-renderer.py").is_file())

    def test_spine_viewer_release_layout_is_cli_only(self):
        tool_dir = self.tool / "tools/SpineViewer"
        for relative in (
            "PortraitSceneCLI.dll",
            "PortraitSceneCLI.runtimeconfig.json",
            "PortraitSceneCLI.deps.json",
            "Spine.dll",
            "SpineRuntime21.dll",
            "SpineRuntime42.dll",
            "SFML.Graphics.dll",
            "SFML.System.dll",
            "SFML.Window.dll",
            "SkiaSharp.dll",
            "System.Text.Json.dll",
            "System.Text.Encodings.Web.dll",
            "System.IO.Pipelines.dll",
            "NLog.dll",
            "Spectre.Console.dll",
            "System.CommandLine.dll",
            "FFMpegCore.dll",
            "Instances.dll",
            "PsdWriter.dll",
            "runtimes/linux-x64/native/libcsfml-graphics.so",
            "runtimes/linux-x64/native/libsfml-window.so.2.6",
            "runtimes/linux-x64/native/libSkiaSharp.so",
        ):
            with self.subTest(relative=relative):
                self.assertTrue((tool_dir / relative).is_file(), relative)
        for relative in (
            "SpineViewer.exe",
            "SpineViewer.dll",
            "SpineViewerCLI.exe",
            "SpineViewerCLI.dll",
            "SpineViewerCLI.runtimeconfig.json",
            "SpineViewerCLI.deps.json",
            "PresentationFramework.dll",
            "libSkiaSharp.dll",
            "ffmpeg.exe",
            "runtimes/win-x64/native/libSkiaSharp.dll",
            "runtimes/osx-x64/native/libSkiaSharp.dylib",
        ):
            with self.subTest(relative=relative):
                self.assertFalse((tool_dir / relative).exists(), relative)

    def test_linux_query_bundle_layout(self):
        bundle = self.tool / "tools/SpineViewer/linux"
        for relative in (
            "SpineViewerCLI.dll",
            "SpineViewerCLI.runtimeconfig.json",
            "SpineViewerCLI.deps.json",
            "Spine.dll",
            "SpineRuntime42.dll",
            "SFML.Graphics.dll",
            "SkiaSharp.dll",
            "Spectre.Console.dll",
            "System.CommandLine.dll",
            "NLog.dll",
            "System.Text.Json.dll",
            "runtimes/linux-x64/native/libSkiaSharp.so",
        ):
            with self.subTest(relative=relative):
                self.assertTrue((bundle / relative).is_file(), relative)

    def test_asset_studio_release_layout_is_cli_only(self):
        tool_dir = self.tool / "tools/AssetStudio"
        for relative in (
            "AssetStudio.CLI.dll",
            "AssetStudio.CLI.deps.json",
            "AssetStudio.CLI.runtimeconfig.json",
            "AssetStudio.dll",
            "Keys.json",
            "AI/versions.json",
            "runtimes/linux-x64/native/libglfw.so.3.3",
        ):
            with self.subTest(relative=relative):
                self.assertTrue((tool_dir / relative).is_file(), relative)
        for relative in (
            "AssetStudio.GUI.dll",
            "AssetStudio.GUI.deps.json",
            "AssetStudio.GUI.exe",
            "AssetStudio.CLI.exe",
            "AssetStudio.CLI.pdb",
            "log.txt",
            "x64/AssetStudio.FBXNative.dll",
            "x86/AssetStudio.FBXNative.dll",
            "zh-Hans/System.CommandLine.resources.dll",
            "runtimes/win-x64/native/e_sqlite3.dll",
        ):
            with self.subTest(relative=relative):
                self.assertFalse((tool_dir / relative).exists(), relative)

    def test_lua_tools_ship_and_tool_project_files_stay_out(self):
        self.assertTrue((self.tool / "tools/lua/unluac.jar").is_file())
        self.assertTrue((self.tool / "tools/lua/opmap").is_file())
        self.assertFalse((self.tool / "README.md").exists())
        self.assertFalse((self.tool / "requirements.txt").exists())
        self.assertFalse((self.tool / "tests/test_preview.py").exists())


class ToolRuntimeMappingTests(unittest.TestCase):
    """Tool runtime files redeploy the updater; GUI/docs/tests must not."""

    RUNTIME_PATHS = (
        "xl_updata_tool/app/__init__.py",
        "xl_updata_tool/app/features/preview/__init__.py",
        "xl_updata_tool/app/features/preview/portrait_headless.py",
        "xl_updata_tool/app/features/preview/portrait_config/builder.py",
        "xl_updata_tool/app/features/preview/portrait_config/model.py",
        "xl_updata_tool/app/platform/portrait_render_process.py",
        "xl_updata_tool/app/platform/processes.py",
        "xl_updata_tool/app/shared/lua.py",
        "xl_updata_tool/app/features/characters/parser/__init__.py",
        "xl_updata_tool/native/portrait_scene/Program.cs",
        "xl_updata_tool/native/portrait_scene/DiskCanvas.cs",
        "xl_updata_tool/scripts/build-portrait-scene-renderer.py",
        "xl_updata_tool/tools/lua/unluac.jar",
        "xl_updata_tool/tools/AssetStudio/AssetStudio.CLI.dll",
        "xl_updata_tool/tools/AssetStudio/runtimes/linux-x64/native/libglfw.so.3.3",
        "xl_updata_tool/tools/SpineViewer/PortraitSceneCLI.dll",
        "xl_updata_tool/tools/SpineViewer/PortraitSceneCLI.runtimeconfig.json",
        "xl_updata_tool/tools/SpineViewer/PortraitSceneCLI.deps.json",
        "xl_updata_tool/tools/SpineViewer/System.Text.Json.dll",
        "xl_updata_tool/tools/SpineViewer/linux/SpineViewerCLI.dll",
        "xl_updata_tool/tools/SpineViewer/SpineRuntime42.dll",
        "xl_updata_tool/tools/SpineViewer/runtimes/linux-x64/native/libcsfml-graphics.so",
    )

    NON_RUNTIME_PATHS = (
        "xl_updata_tool/README.md",
        "xl_updata_tool/docs/usage.md",
        "xl_updata_tool/requirements.txt",
        "xl_updata_tool/requirements-dev.txt",
        "xl_updata_tool/tests/test_portrait_headless_unittest.py",
        "xl_updata_tool/tests/fixtures/sample.json",
        "xl_updata_tool/app/features/preview/controller.py",
        "xl_updata_tool/app/features/preview/service.py",
        "xl_updata_tool/app/features/preview/dialogs/export_dialog.py",
        "xl_updata_tool/app/features/preview/workers/export_worker.py",
        "xl_updata_tool/app/features/preview/fgui.py",
        "xl_updata_tool/app/features/characters/page.py",
        "xl_updata_tool/app/main.py",
        "xl_updata_tool/app/features/preview/portrait_config/import_service.py",
        "xl_updata_tool/tools/SpineViewer/SpineViewer.exe",
        "xl_updata_tool/tools/SpineViewer/SpineViewer.dll",
        "xl_updata_tool/tools/SpineViewer/libSkiaSharp.dll",
        "xl_updata_tool/tools/SpineViewer/NLog.Windows.Wpf.dll",
        "xl_updata_tool/tools/SpineViewer/ffmpeg.exe",
        "xl_updata_tool/tools/SpineViewer/runtimes/win-x64/native/libSkiaSharp.dll",
        "xl_updata_tool/tools/AssetStudio/AssetStudio.GUI.dll",
        "xl_updata_tool/tools/AssetStudio/AssetStudio.CLI.exe",
        "xl_updata_tool/tools/AssetStudio/AssetStudio.CLI.pdb",
        "xl_updata_tool/tools/AssetStudio/log.txt",
        "xl_updata_tool/tools/AssetStudio/x64/AssetStudio.FBXNative.dll",
        "xl_updata_tool/tools/AssetStudio/x86/AssetStudio.FBXNative.dll",
        "xl_updata_tool/tools/AssetStudio/zh-Hans/System.CommandLine.resources.dll",
        "xl_updata_tool/tools/AssetStudio/runtimes/win-x64/native/e_sqlite3.dll",
        "xl_updata_tool/tools/vgmstream/vgmstream-cli.exe",
        "xl_updata_tool/tools/epic7_debank_v1_0/quickbms.exe",
    )

    def test_rule_membership_matches_expectations(self):
        for path in self.RUNTIME_PATHS:
            with self.subTest(path=path):
                self.assertTrue(is_tool_runtime_path(path), path)
        for path in self.NON_RUNTIME_PATHS:
            with self.subTest(path=path):
                self.assertFalse(is_tool_runtime_path(path), path)

    def test_windows_separators_are_normalized(self):
        self.assertTrue(is_tool_runtime_path(
            "xl_updata_tool\\app\\platform\\portrait_render_process.py"))

    def test_legacy_planner_maps_tool_runtime_to_backend_only(self):
        for branch, units in (("test", BACKEND_TEST), ("main", BACKEND_MAIN)):
            for path in ("xl_updata_tool/app/features/preview/portrait_headless.py",
                         "xl_updata_tool/native/portrait_scene/Program.cs",
                         "xl_updata_tool/tools/SpineViewer/PortraitSceneCLI.dll"):
                with self.subTest(branch=branch, path=path):
                    plan = plan_deployment(branch, "a" * 40, [path])
                    self.assertEqual(plan.impacted_units, units)
        for branch in ("test", "main"):
            for path in ("xl_updata_tool/app/features/preview/controller.py",
                         "xl_updata_tool/tests/test_portrait_headless_unittest.py",
                         "xl_updata_tool/README.md",
                         "xl_updata_tool/tools/SpineViewer/SpineViewer.exe",
                         "xl_updata_tool/tools/AssetStudio/AssetStudio.GUI.dll"):
                with self.subTest(branch=branch, path=path):
                    plan = plan_deployment(branch, "a" * 40, [path])
                    self.assertEqual(plan.impacted_units, ())
                    self.assertEqual(plan.impacted_tiers, ())
                    self.assertEqual(plan.announcement_scope, ())

    def test_legacy_planner_mixed_tool_and_server_paths_deploy_backend_once(self):
        plan = plan_deployment(
            "test", "a" * 40,
            ["xl_updata_server/server_app/processor.py",
             "xl_updata_tool/app/platform/processes.py"])
        self.assertEqual(plan.impacted_units, BACKEND_TEST)

    def test_debug_branch_never_deploys_tool_runtime_changes(self):
        plan = plan_deployment(
            "debug", "a" * 40, ["xl_updata_tool/app/features/preview/portrait_headless.py"])
        self.assertEqual(plan.impacted_units, ())

    def test_configured_inventory_maps_tool_runtime_to_backend_units(self):
        values = {
            "instances": [
                {"instance_id": "test-a", "watch_ref": "test",
                 "config_path": "test-a/config.toml", "data_roots": ["test-a/data"],
                 "backend_config_path": "test-a/updater.toml",
                 "units": {"bot": "xl-test-a.service", "backend": "xl-updata-server-test.service"},
                 "listen_ports": {"bot": 8811, "backend": 8791}, "groups": ["g-a"]},
            ],
            "gateway": {"unit": "xl-gateway.service", "watch_ref": "main",
                        "config_path": "gateway/config.toml", "listen_port": 8810,
                        "data_roots": ["gateway/data"]},
        }
        with tempfile.TemporaryDirectory() as temp:
            inventory = config.DeploymentInventory.from_mapping(
                values, base_dir=Path(temp))
            instance = inventory.instances[0]
            instance.backend_config_path.parent.mkdir(parents=True, exist_ok=True)
            instance.backend_config_path.write_text(
                '[server]\ninstance_id = "test-a"\n[paths]\ndata_dir = "data"\n'
                '[api]\nenabled = true\nhost = "127.0.0.1"\nport = 8791\n',
                encoding="utf-8")
            instance.config_path.parent.mkdir(parents=True, exist_ok=True)
            instance.config_path.write_text(
                '[instance]\nid = "test-a"\n[watch]\ndata_dir = "data"\n'
                'outbox_dir = "data/outbox"\n', encoding="utf-8")
            inventory.gateway.config_path.parent.mkdir(parents=True, exist_ok=True)
            inventory.gateway.config_path.write_text(
                '[watch]\ndata_dir = "data"\n[gateway]\ncontrol_port = 8810\n'
                'control_token = "token-01234567890123456789012345678901"\n'
                '[router]\ndeployment_control_port = 8814\n'
                '[router.group_instances]\n"g-a" = "test-a"\n'
                '[router.instance_ports]\n"test-a" = 8811\n'
                '[gateway.instance_tokens]\n"test-a" = "token-test-a-01234567890123456789"\n',
                encoding="utf-8")

            plan = plan_deployment(
                "test", "a" * 40,
                ["xl_updata_tool/app/features/preview/portrait_headless.py"],
                inventory=inventory)
            self.assertEqual(plan.impacted_units, BACKEND_TEST)
            self.assertNotIn("xl-test-a.service", plan.impacted_units)

            plan = plan_deployment(
                "test", "a" * 40,
                ["xl_updata_tool/app/features/preview/controller.py"],
                inventory=inventory)
            self.assertEqual(plan.impacted_units, ())

            plan = plan_deployment(
                "test", "a" * 40,
                ["xl_updata_tool/tools/AssetStudio/AssetStudio.CLI.dll"],
                inventory=inventory)
            self.assertEqual(plan.impacted_units, BACKEND_TEST)


if __name__ == "__main__":
    unittest.main()
