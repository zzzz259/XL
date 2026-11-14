"""Exact, version-bound resolution of Unity PPtrs and catalog dependencies."""

from __future__ import annotations

from dataclasses import dataclass, replace
from typing import Any

from .asset_index import AssetIndex, IndexedAsset
from .model import AssetRef, SceneDiagnostic
from .unity_scene import UnityObjectPointer, UnitySceneParseResult


@dataclass(frozen=True, slots=True)
class ResolvedReference:
    source: AssetRef
    field_path: str
    original: Any
    target: AssetRef | None
    asset: IndexedAsset | None
    status: str
    evidence: str = ""


@dataclass(frozen=True, slots=True)
class ResolvedScene:
    scene: UnitySceneParseResult
    references: tuple[ResolvedReference, ...]
    visited: tuple[AssetRef, ...]
    diagnostics: tuple[SceneDiagnostic, ...]
    complete: bool


def _external_for(pointer: UnityObjectPointer, scene: UnitySceneParseResult):
    if pointer.serialized_file_key:
        matches = [
            entry for entry in scene.externals
            if entry.file_id == pointer.file_id
            and entry.serialized_file_key == pointer.serialized_file_key
        ]
        values = {(entry.name, entry.path) for entry in matches}
        if len(values) > 1:
            return "ambiguous", None
        if not values:
            return "missing", None
        return "ok", next(iter(values))
    if pointer.external_name or pointer.external_path:
        # The parser captured the table entry from the serialized file that
        # owns this PPtr; another SerializedFile may reuse the same fileID.
        return "ok", (pointer.external_name, pointer.external_path)
    # Never consult a scene-wide table without its owning SerializedFile key:
    # fileID is local and can name different bundles in separate files.
    return "missing", None


def _lookup(index: AssetIndex, bundle_hash: str, path_id: int):
    matches = index.find_exact(bundle_hash, path_id)
    if not matches:
        return ("path_id_missing" if index.has_bundle(bundle_hash) else "bundle_unknown"), None
    if len(matches) != 1:
        return "ambiguous_mapping", None
    asset = matches[0]
    if not asset.bundle_present:
        return "bundle_missing", asset
    return "resolved", asset


def resolve_scene_assets(scene: UnitySceneParseResult, index: AssetIndex) -> ResolvedScene:
    """Resolve only exact PPtr/dependency identities; never use names as fallback."""
    references: list[ResolvedReference] = []
    diagnostics = list(scene.diagnostics)
    version_context_valid = (
        scene.version_timestamp is not None
        and scene.version_timestamp == index.version_timestamp
    )
    if scene.version_timestamp is None:
        diagnostics.append(SceneDiagnostic(
            "dependency_version_context_missing",
            "Cannot resolve scene assets without a selected version timestamp",
        ))
    elif scene.version_timestamp != index.version_timestamp:
        diagnostics.append(SceneDiagnostic(
            "dependency_version_mismatch",
            "Scene and asset index belong to different versions",
            details=(
                ("scene_version", str(scene.version_timestamp)),
                ("index_version", str(index.version_timestamp)),
            ),
        ))
    visited: list[AssetRef] = []
    visited_set: set[AssetRef] = set()
    queued: list[tuple[AssetRef, str, Any, str]] = []
    parsed_sources = {pointer.source for pointer in scene.references}
    for pointer in scene.references:
        queued.append((pointer.source, pointer.field_path, pointer, "unity"))  # type: ignore[arg-type]

    # Use a queue plus identity set: cycles are safe and output order follows
    # the deterministic source/reference order.
    cursor = 0
    while cursor < len(queued):
        source, field_path, original, kind = queued[cursor]
        cursor += 1
        if kind == "unity":
            pointer: UnityObjectPointer = original
            if scene.version_timestamp is None:
                references.append(ResolvedReference(
                    source, field_path, pointer, None, None, "version_context_missing",
                    "scene parse was not bound to a selected version timestamp",
                ))
                continue
            if scene.version_timestamp != index.version_timestamp:
                references.append(ResolvedReference(
                    source, field_path, pointer, None, None, "version_mismatch",
                    f"scene version {scene.version_timestamp}; index version {index.version_timestamp}",
                ))
                continue
            if pointer.file_id == 0:
                bundle_hash = source.bundle_hash
                status, asset = _lookup(index, bundle_hash, pointer.path_id)
            elif pointer.file_id < 0:
                references.append(ResolvedReference(
                    source, field_path, pointer, None, None, "external_file_id_invalid",
                ))
                continue
            else:
                ext_status, external = _external_for(pointer, scene)
                if ext_status == "missing":
                    references.append(ResolvedReference(
                        source, field_path, pointer, None, None, "external_file_id_invalid",
                    ))
                    continue
                if ext_status == "ambiguous":
                    references.append(ResolvedReference(
                        source, field_path, pointer, None, None, "ambiguous_mapping",
                    ))
                    continue
                from .asset_index import canonical_bundle_hash

                identifiers = [item for item in external if item]
                normalized_hashes = [canonical_bundle_hash(item) for item in identifiers]
                if not identifiers or any(item is None for item in normalized_hashes):
                    references.append(ResolvedReference(
                        source, field_path, pointer, None, None, "bundle_unknown",
                        " | ".join(identifiers),
                    ))
                    continue
                if len(set(normalized_hashes)) != 1:
                    references.append(ResolvedReference(
                        source, field_path, pointer, None, None, "ambiguous_mapping",
                        "external name and path identify different bundle hashes",
                    ))
                    continue
                bundle_hash = normalized_hashes[0]
                matches = index.find_external(identifiers[0], pointer.path_id)
                if len(matches) > 1:
                    status, asset = "ambiguous_mapping", None
                elif matches:
                    asset = matches[0]
                    bundle_hash = asset.bundle_hash
                    status = "resolved" if asset.bundle_present else "bundle_missing"
                else:
                    # These statuses describe missing evidence only; they do
                    # not bind a CAB to a physical bundle by its filename.
                    exact = index.find_exact(bundle_hash, pointer.path_id)
                    if exact and not any(item.bundle_present for item in exact):
                        status = "bundle_missing"
                    else:
                        status = "path_id_missing" if index.has_bundle(bundle_hash) else "bundle_unknown"
                    asset = None
            target = AssetRef(bundle_hash, pointer.path_id) if status == "resolved" else None
            references.append(ResolvedReference(
                source, field_path, pointer, target, asset, status,
                asset.evidence if asset else f"{bundle_hash}:{pointer.path_id}",
            ))
            if asset is not None and status == "resolved":
                if _visit(asset, visited, visited_set):
                    _queue_catalog_dependencies(asset, queued)
                    _queue_object_references(asset, index, parsed_sources, queued, diagnostics)
            continue

        dependency = original
        if not isinstance(dependency, dict) or set(dependency) != {"bundle_hash", "path_id"}:
            references.append(ResolvedReference(
                source, field_path, dependency, None, None,
                "unsupported_dependency_reference_encoding",
            ))
            continue
        bundle_hash = str(dependency.get("bundle_hash", ""))
        path_id = dependency["path_id"]
        if not isinstance(path_id, int) or isinstance(path_id, bool) or path_id == 0:
            references.append(ResolvedReference(
                source, field_path, dependency, None, None,
                "unsupported_dependency_reference_encoding",
            ))
            continue
        if scene.version_timestamp is None:
            status, asset = "version_context_missing", None
        elif scene.version_timestamp != index.version_timestamp:
            status, asset = "version_mismatch", None
        else:
            status, asset = _lookup(index, bundle_hash, path_id)
        target = AssetRef(bundle_hash, path_id) if status == "resolved" else None
        references.append(ResolvedReference(
            source, field_path, dependency, target, asset, status,
            asset.evidence if asset else f"{bundle_hash}:{path_id}",
        ))
        if asset is not None and status == "resolved":
            if _visit(asset, visited, visited_set):
                _queue_catalog_dependencies(asset, queued)
                _queue_object_references(asset, index, parsed_sources, queued, diagnostics)

    diagnostics = [item for item in diagnostics if not _resolved_external_diagnostic(item, references)]
    for item in references:
        if item.status != "resolved":
            diagnostics.append(SceneDiagnostic(
                f"dependency_{item.status}",
                f"Unresolved Unity dependency at {item.field_path}: {item.status}",
                source=item.source,
                details=(("field", item.field_path), ("evidence", item.evidence)),
            ))
    complete = (
        version_context_valid
        and all(item.status == "resolved" for item in references)
        and not any(item.severity == "error" for item in diagnostics)
    )
    return ResolvedScene(
        scene, tuple(references), tuple(visited), tuple(diagnostics),
        complete,
    )


def _visit(asset: IndexedAsset, visited: list[AssetRef], visited_set: set[AssetRef]) -> bool:
    ref = AssetRef(asset.bundle_hash, asset.path_id)
    if ref not in visited_set:
        visited_set.add(ref)
        visited.append(ref)
        return True
    return False


def _resolved_external_diagnostic(diagnostic, references) -> bool:
    """Retire a parser's provisional PPtr error only after exact resolution."""
    if diagnostic.code != "unresolved_external_pptr":
        return False
    details = dict(diagnostic.details)
    field = details.get("field", "").casefold()
    for reference in references:
        pointer = reference.original
        if reference.status != "resolved" or reference.source != diagnostic.source:
            continue
        if not isinstance(pointer, UnityObjectPointer):
            continue
        path = reference.field_path.casefold()
        if (str(pointer.file_id) == details.get("file_id")
                and str(pointer.path_id) == details.get("path_id")
                and (path == field or path.startswith(field + "[") or path.startswith(field + "."))):
            return True
    return False


def _queue_catalog_dependencies(asset: IndexedAsset, queued: list[tuple[AssetRef, str, Any, str]]) -> None:
    source = AssetRef(asset.bundle_hash, asset.path_id)
    for index, dependency in enumerate(asset.dependencies):
        queued.append((source, f"dependencies[{index}]", dependency, "catalog"))


def _queue_object_references(asset, index, parsed_sources, queued, diagnostics) -> None:
    source = AssetRef(asset.bundle_hash, asset.path_id)
    if source in parsed_sources:
        return
    try:
        pointers = index.object_references(asset)
    except Exception as error:
        diagnostics.append(SceneDiagnostic(
            "dependency_metadata_read_failed", f"Cannot inspect required metadata object: {error}",
            source=source,
        ))
        return
    for pointer in pointers:
        # The PPtr captures the external table entry directly from this object's
        # SerializedFile. It does not belong to the root scene's external table.
        pointer = replace(pointer, serialized_file_key="")
        queued.append((source, pointer.field_path, pointer, "unity"))
