import copy
import json
from pathlib import Path
import tempfile
import unittest
from app.features.preview.portrait_config.asset_index import AssetIndex

H = "a" * 32
C = "b" * 32


def fixture():
    return dict(
        schema="portrait-exact-catalog/v1",
        version_timestamp=1,
        serialized_files=[
            dict(bundle_hash=H, serialized_file="CAB-" + C, externals=[], preload=[dict(file_id=0, path_id=-2)])
        ],
        assets=[
            dict(
                bundle_hash=H,
                serialized_file="CAB-" + C,
                path_id=i,
                name=n,
                asset_type=t,
                source="verified fixture",
                version_timestamp=1,
            )
            for i, n, t in [(1, "root", "GameObject"), (-2, "shared", "Texture2D"), (9, "bundle", "AssetBundle")]
        ],
        container_entries=[
            dict(
                path=p,
                owner_bundle_hash=H,
                owner_serialized_file="CAB-" + C,
                asset_bundle_path_id=9,
                ordinal=i,
                asset=dict(file_id=0, path_id=1),
                preload_index=0,
                preload_size=1,
            )
            for i, p in enumerate(["Assets/A.prefab", "Assets/B.prefab"])
        ],
    )


class ExactCatalogTests(unittest.TestCase):
    def load(self, payload):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            (root / (H + ".bundle")).write_bytes(b"fixture")
            catalog = root / "catalog.json"
            catalog.write_text(json.dumps(payload))
            loader = AssetIndex.from_exact_catalog if "schema" in payload else AssetIndex.from_catalog
            return loader(catalog, root, 1)

    def test_aliases_share_one_physical_identity(self):
        index = self.load(fixture())
        self.assertEqual(len(index.find_by_container_exact("A.prefab")), 2)
        self.assertEqual(len(index.find_exact(H, -2)), 1)
        self.assertEqual(index.resolve("assets/b.prefab", "shared").path_id, -2)
        entries = index.find_container_entries_exact("A.prefab")
        self.assertEqual(len(entries), 1)
        self.assertEqual([e.role for e in entries[0].edges], ["direct", "preload"])
        self.assertEqual(len(index.container_members(entries)), 2)

    def test_conflicting_physical_rows_rejected(self):
        p = fixture()
        row = copy.deepcopy(p["assets"][0])
        row["name"] = "different"
        p["assets"].append(row)
        with self.assertRaises(ValueError):
            self.load(p)

    def test_invalid_pointer_and_preload_rejected(self):
        for key, value in [("preload_index", -1), ("preload_size", 2), ("ordinal", True)]:
            with self.subTest(key=key):
                p = fixture()
                p["container_entries"][0][key] = value
                with self.assertRaises(ValueError):
                    self.load(p)
        p = fixture()
        p["container_entries"][0]["asset"]["file_id"] = -1
        with self.assertRaises(ValueError):
            self.load(p)

    def test_malformed_ledger_never_legacy(self):
        p = fixture()
        del p["container_entries"]
        with self.assertRaises(ValueError):
            self.load(p)

    def test_legacy_duplicate_ambiguity_preserved(self):
        row = dict(bundle_hash=H, path_id=1, name="x", container="a")
        index = self.load(dict(assets=[row, row]))
        self.assertEqual(len(index.find_exact(H, 1)), 2)
        with self.assertRaises(ValueError):
            index.resolve("a", "x")


class ScopeTests(unittest.TestCase):
    def test_root_renderer_and_missing_required_reference_kept(self):
        from app.features.preview.portrait_config.builder import _scope_to_container
        from app.features.preview.portrait_config.model import AssetRef
        from app.features.preview.portrait_config.unity_scene import (
            UnitySceneParseResult,
            UnityScenePart,
            UnityObjectPointer,
        )

        a, b = AssetRef(H, 1), AssetRef(H, 2)
        parts = tuple(
            UnityScenePart(go, AssetRef(H, i + 10), str(i), "MeshRenderer", str(i), (), (0, 0, 0), True, 0, 0, 0)
            for i, go in enumerate((a, b))
        )
        pointer = UnityObjectPointer(parts[0].component_ref, "m_Materials", 0, 99)
        parsed = UnitySceneParseResult(
            H,
            ((a, "a"), (b, "b")),
            (),
            parts,
            (),
            (),
            references=(pointer,),
            container_roots=(("assets/a.prefab", a), ("assets/b.prefab", b)),
            object_ancestry=((a, ()), (b, ())),
        )
        index = ExactCatalogTests().load(fixture())
        from dataclasses import replace

        parsed = replace(parsed, serialized_file="CAB-" + C)
        scoped = _scope_to_container(parsed, (), index, prefab_name="assets/a.prefab", prefab_ref=a)
        self.assertEqual(scoped.parts, (parts[0],))
        self.assertEqual(scoped.references, (pointer,))

    def test_identity_export_without_legacy_container(self):
        from app.features.preview.portrait_config.asset_index import IndexedAsset
        from app.features.preview.portrait_config.model import AssetRef
        from app.features.preview.portrait_scene_export import _resolve_asset_path

        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            path = root / "_portrait_inputs" / H / "1" / "x"
            path.parent.mkdir(parents=True)
            path.write_bytes(b"x")
            asset = IndexedAsset("x", "", H, 1, (), 1, root / "bundle", True, "fixture")
            self.assertEqual(_resolve_asset_path(AssetIndex(1, (asset,)), root, AssetRef(H, 1)), path)


class RootSelectionTests(ExactCatalogTests):
    def scene(self, p):
        from unittest.mock import patch
        from app.features.preview.portrait_config.builder import build_portrait_scenes
        from app.features.preview.portrait_config.lua_config import PortraitRoot
        from app.features.preview.portrait_config.unity_scene import UnitySceneParseResult

        index = self.load(p)
        root = PortraitRoot("1", 1, "A.prefab", None, (), "fixture")
        with patch(
            "app.features.preview.portrait_config.builder.parse_unity_scene",
            return_value=UnitySceneParseResult(H, (), (), (), (), (), serialized_file="CAB-" + C),
        ):
            return build_portrait_scenes(1, [root], index)[0]

    def test_missing_direct_root_fails_before_parser(self):
        p = fixture()
        p["container_entries"][0]["asset"]["path_id"] = -2
        self.assertIn("prefab_root_unresolved", [d.code for d in self.scene(p).diagnostics])

    def test_mismatched_actual_root_fails(self):
        self.assertIn("exact_prefab_root_mismatch", [d.code for d in self.scene(fixture()).diagnostics])

    def test_distinct_leaf_paths_ambiguous(self):
        p = fixture()
        p["container_entries"][1]["path"] = "Other/A.prefab"
        self.assertIn("ambiguous_prefab_asset", [d.code for d in self.scene(p).diagnostics])

    def test_multiple_serialized_files_rejected(self):
        p = fixture()
        r = copy.deepcopy(p["serialized_files"][0])
        r["serialized_file"] = "CAB-" + "c" * 32
        p["serialized_files"].append(r)
        with self.assertRaises(ValueError):
            self.load(p)

    def test_null_signed_and_builtin_evidence(self):
        p = fixture()
        p["serialized_files"][0]["externals"] = ["Resources/unity_builtin_extra"]
        p["serialized_files"][0]["preload"] = [dict(file_id=1, path_id=-12)]
        p["container_entries"][0]["asset"]["path_id"] = 0
        index = self.load(p)
        entry = index.find_container_entries_exact("A.prefab")[0]
        self.assertEqual([e.unresolved_reason for e in entry.edges], ["null", "engine_builtin"])
        self.assertEqual(entry.edges[1].path_id, -12)

    def test_bool_path_and_malformed_archive_rejected(self):
        for edit in ("pid", "archive"):
            p = fixture()
            if edit == "pid":
                p["assets"][0]["path_id"] = True
            else:
                p["serialized_files"][0]["externals"] = ["archive:/CAB-" + "a" * 32 + "/CAB-" + "c" * 32]
            with self.assertRaises(ValueError):
                self.load(p)


class ParserOwnershipTests(unittest.TestCase):
    def test_multiple_actual_serialized_files_fail_before_pathid_map(self):
        from unittest.mock import patch
        from types import SimpleNamespace
        from app.features.preview.portrait_config.unity_scene import parse_unity_scene

        objects = [
            SimpleNamespace(
                path_id=1, type=SimpleNamespace(name="Texture2D"), assets_file=SimpleNamespace(name="CAB-" + c * 32)
            )
            for c in ("a", "b")
        ]
        with patch(
            "app.features.preview.portrait_config.unity_scene._load_environment",
            return_value=SimpleNamespace(objects=objects),
        ):
            parsed = parse_unity_scene(Path("fixture"), H, 1)
        self.assertIn("unsupported_serialized_file_ownership", [d.code for d in parsed.diagnostics])


class ExternalAndCacheTests(ExactCatalogTests):
    def external_fixture(self):
        p = fixture()
        other = "d" * 32
        othercab = "e" * 32
        p["serialized_files"].append(
            dict(bundle_hash=other, serialized_file="CAB-" + othercab, externals=[], preload=[])
        )
        p["assets"].append(
            dict(
                bundle_hash=other,
                serialized_file="CAB-" + othercab,
                path_id=-8,
                name="external",
                asset_type="Texture2D",
                source="fixture",
                version_timestamp=1,
            )
        )
        p["serialized_files"][0]["externals"] = ["archive:/CAB-" + othercab + "/CAB-" + othercab]
        p["serialized_files"][0]["preload"] = [dict(file_id=1, path_id=-8)]
        return p

    def test_external_member_does_not_change_owner(self):
        p = self.external_fixture()
        index = self.load(p)
        entries = index.find_container_entries_exact("A.prefab")
        self.assertEqual(entries[0].owner_bundle_hash, H)
        self.assertEqual({a.bundle_hash for a in index.container_members(entries)}, {H, "d" * 32})

    def test_used_cab_collision_rejected(self):
        p = self.external_fixture()
        row = copy.deepcopy(p["serialized_files"][1])
        row["bundle_hash"] = "f" * 32
        p["serialized_files"].append(row)
        with self.assertRaises(ValueError):
            self.load(p)

    def test_repeated_direct_evidence_dedupes_members_only(self):
        p = fixture()
        row = copy.deepcopy(p["container_entries"][0])
        row["ordinal"] = 4
        p["container_entries"].append(row)
        index = self.load(p)
        entries = index.find_container_entries_exact("A.prefab")
        self.assertEqual(len(entries), 2)
        self.assertEqual(len(index.container_members(entries)), 2)

    def test_distinct_same_name_still_ambiguous(self):
        p = fixture()
        p["assets"][0]["name"] = "shared"
        with self.assertRaises(ValueError):
            self.load(p).resolve("assets/a.prefab", "shared")

    def test_cache_input_order_deterministic_binding_change_invalidates(self):
        from app.features.preview.portrait_config.builder import _root_config_hash
        from app.features.preview.portrait_config.lua_config import PortraitRoot

        root = PortraitRoot("1", 1, "A.prefab", None, (), "fixture")
        p = fixture()
        a = self.load(p)
        p["container_entries"].reverse()
        p["assets"].reverse()
        b = self.load(p)

        def digest(index):
            return _root_config_hash(root, H, index.container_members(index.container_entries), index.container_entries)

        self.assertEqual(digest(a), digest(b))
        p["container_entries"][0]["asset"]["path_id"] = -2
        self.assertNotEqual(digest(a), digest(self.load(p)))


class IdentitySafetyTests(unittest.TestCase):
    def test_invalid_pathid_rejected(self):
        from app.features.preview.portrait_config.asset_index import IndexedAsset
        from app.features.preview.portrait_config.extracted_paths import identity_output_path

        for pid in (True, 0, "../escape", 2**63):
            with self.subTest(pid=pid):
                with self.assertRaises(ValueError):
                    identity_output_path(IndexedAsset("x", "", H, pid, (), 1, Path("x"), True, "fixture"))


class OwnerVerificationTests(unittest.TestCase):
    def test_exact_scope_rejects_actual_owner_mismatch(self):
        from app.features.preview.portrait_config.builder import _scope_to_container
        from app.features.preview.portrait_config.asset_index import IndexedAsset
        from app.features.preview.portrait_config.model import AssetRef
        from app.features.preview.portrait_config.unity_scene import UnitySceneParseResult, UnityScenePart

        ref = AssetRef(H, 1)
        part = UnityScenePart(ref, AssetRef(H, 10), "root", "MeshRenderer", "root", (), (0, 0, 0), True, 0, 0, 0)
        parsed = UnitySceneParseResult(
            H, ((ref, "root"),), (), (part,), (), (), container_roots=(("assets/a.prefab", ref),)
        )
        asset = IndexedAsset("root", "", H, 1, (), 1, Path("fixture"), True, "fixture", "GameObject", C)
        scoped = _scope_to_container(
            parsed,
            (),
            AssetIndex(1, (asset,), exact_schema="portrait-exact-catalog/v1"),
            prefab_name="assets/a.prefab",
            prefab_ref=ref,
        )
        self.assertFalse(scoped.parts)
        self.assertIn("exact_prefab_owner_mismatch", [d.code for d in scoped.diagnostics])


class RootContractTests(ExactCatalogTests):
    def build(self, payload, parsed):
        from unittest.mock import patch
        from app.features.preview.portrait_config.builder import build_portrait_scenes
        from app.features.preview.portrait_config.lua_config import PortraitRoot

        with patch("app.features.preview.portrait_config.builder.parse_unity_scene", return_value=parsed):
            return build_portrait_scenes(
                1, [PortraitRoot("1", 1, "A.prefab", None, (), "fixture")], self.load(payload)
            )[0]

    def parsed(self):
        from app.features.preview.portrait_config.model import AssetRef
        from app.features.preview.portrait_config.unity_scene import UnitySceneParseResult, UnityScenePart

        ref = AssetRef(H, 1)
        part = UnityScenePart(ref, AssetRef(H, 10), "root", "MeshRenderer", "root", (), (0, 0, 0), True, 0, 0, 0)
        return UnitySceneParseResult(
            H,
            ((ref, "root"),),
            (),
            (part,),
            (),
            (),
            container_roots=(("assets/a.prefab", ref),),
            serialized_file="CAB-" + C,
        )

    def test_repeated_root_and_direct_resource_keep_exact_prefab(self):
        p = fixture()
        for ordinal, pid in ((2, 1), (3, -2)):
            entry = copy.deepcopy(p["container_entries"][0])
            entry["ordinal"] = ordinal
            entry["asset"]["path_id"] = pid
            p["container_entries"].append(entry)
        scene = self.build(p, self.parsed())
        self.assertEqual(scene.prefab.path_id, 1)
        self.assertEqual(scene.prefab_container, "assets/a.prefab")
        self.assertEqual(len(scene.parts), 1)
        self.assertIn("exact_container_bindings", dict(scene.config_metadata))

    def test_preload_only_and_two_roots_rejected(self):
        p = fixture()
        p["container_entries"][0]["asset"]["path_id"] = -2
        p["serialized_files"][0]["preload"] = [dict(file_id=0, path_id=1)]
        self.assertIn("prefab_root_unresolved", [d.code for d in self.build(p, self.parsed()).diagnostics])
        p = fixture()
        row = copy.deepcopy(p["assets"][0])
        row["path_id"] = 2
        p["assets"].append(row)
        e = copy.deepcopy(p["container_entries"][0])
        e["ordinal"] = 3
        e["asset"]["path_id"] = 2
        p["container_entries"].append(e)
        self.assertIn("ambiguous_prefab_root", [d.code for d in self.build(p, self.parsed()).diagnostics])

    def test_external_direct_root_explicitly_unsupported(self):
        p = ExternalAndCacheTests().external_fixture()
        p["assets"][-1]["asset_type"] = "GameObject"
        p["container_entries"][0]["asset"] = dict(file_id=1, path_id=-8)
        self.assertIn("external_prefab_root_unsupported", [d.code for d in self.build(p, self.parsed()).diagnostics])


class ExternalVerificationTests(unittest.TestCase):
    def test_exact_external_requires_catalog_owner_as_well_as_actual_type(self):
        from unittest.mock import patch
        from app.features.preview.portrait_config.asset_index import IndexedAsset

        asset = IndexedAsset("x", "", H, 1, (), 1, Path("fixture"), True, "fixture", "Texture2D", C)
        index = AssetIndex(1, (asset,), exact_schema="portrait-exact-catalog/v1")
        with patch.object(AssetIndex, "_serialized_identities", return_value={("d" * 32, 1): "Texture2D"}):
            self.assertEqual(index.find_external("CAB-" + "d" * 32, 1), ())

    def test_exact_external_one_shared_identity(self):
        from unittest.mock import patch
        from app.features.preview.portrait_config.asset_index import IndexedAsset

        asset = IndexedAsset("x", "", H, 1, (), 1, Path("fixture"), True, "fixture", "Texture2D", C)
        index = AssetIndex(1, (asset,), exact_schema="portrait-exact-catalog/v1")
        with patch.object(AssetIndex, "_serialized_identities", return_value={(C, 1): "Texture2D"}) as read:
            self.assertEqual(index.find_external("CAB-" + C, 1), (asset,))
            self.assertEqual(index.find_external("CAB-" + C, 1), (asset,))
            self.assertEqual(read.call_count, 1)


class ReviewBoundaryTests(unittest.TestCase):
    def test_oversized_legacy_array_and_object_preserve_loading(self):
        from app.features.preview.portrait_config.exact_catalog import MAX_BYTES

        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            catalog = root / "legacy.json"
            for payload in ("[]", '{"assets":[]}'):
                catalog.write_text(payload + " " * MAX_BYTES)
                self.assertEqual(AssetIndex.from_catalog(catalog, root, 1).assets, ())

    def test_unused_preload_fileid_outside_owner_table_rejected(self):
        from app.features.preview.portrait_config.exact_catalog import parse_catalog

        p = fixture()
        p["container_entries"] = []
        p["serialized_files"][0]["preload"] = [dict(file_id=999, path_id=1)]
        with self.assertRaises(ValueError):
            parse_catalog(p, Path("."), 1)

    def test_explicit_exact_loader_is_available(self):
        self.assertTrue(callable(getattr(AssetIndex, "from_exact_catalog", None)))


class ExplicitIngressTests(unittest.TestCase):
    def test_exact_loader_bounds_read_before_json_decode(self):
        import io
        from unittest.mock import patch, MagicMock

        stream = MagicMock(wraps=io.BytesIO(b" " * 65))
        stream.__enter__.return_value = stream
        with (
            patch("app.features.preview.portrait_config.exact_catalog.MAX_BYTES", 64),
            patch.object(Path, "open", return_value=stream),
            patch("app.features.preview.portrait_config.asset_index.json.loads") as decode,
        ):
            with self.assertRaisesRegex(ValueError, "byte limit"):
                AssetIndex.from_exact_catalog(Path("fixture"), Path("."), 1)
            stream.read.assert_called_once_with(65)
            decode.assert_not_called()

    def test_exact_and_legacy_loaders_never_downgrade_marked_input(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            catalog = root / "catalog.json"
            for payload in (
                {"schema": "portrait-exact-catalog/v1"},
                {"container_entries": []},
                {"serialized_files": []},
            ):
                catalog.write_text(json.dumps(payload))
                for loader in (AssetIndex.from_catalog, AssetIndex.from_exact_catalog):
                    with self.assertRaises(ValueError):
                        loader(catalog, root, 1)
            catalog.write_text("[]")
            with self.assertRaises(ValueError):
                AssetIndex.from_exact_catalog(catalog, root, 1)

    def test_unused_preload_bounds_reject_even_null_and_bool(self):
        from app.features.preview.portrait_config.exact_catalog import parse_catalog

        for fid in (-1, True, 1, 2**31):
            p = fixture()
            p["container_entries"] = []
            p["serialized_files"][0]["preload"] = [dict(file_id=fid, path_id=0)]
            with self.subTest(fid=fid):
                with self.assertRaises(ValueError):
                    parse_catalog(p, Path("."), 1)


if __name__ == "__main__":
    unittest.main()
