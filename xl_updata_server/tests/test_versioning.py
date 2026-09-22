import json

from server_app.versioning import (
    VersionWorkspace,
    publish_version,
    read_current_pointer,
    write_current_pointer,
)


def test_version_workspace_isolated_by_timestamp(tmp_path):
    workspace = VersionWorkspace.create(tmp_path / "versions", 123)

    assert workspace.root.parent == tmp_path / "versions"
    assert workspace.root.name.startswith(".123-")
    assert workspace.bundles_dir == workspace.root / "bundles"
    assert workspace.output_dir.is_dir()
    assert not (workspace.root / "bundles" / "Arts").exists()
    assert not (workspace.root / "portraits").exists()


def test_current_pointer_changes_only_after_success(tmp_path):
    write_current_pointer(tmp_path, 100)
    workspace = VersionWorkspace.create(tmp_path / "versions", 200)
    (workspace.output_dir / "manifest.json").write_text("{}", encoding="utf-8")

    published = publish_version(workspace, tmp_path / "versions")
    write_current_pointer(tmp_path, 200)

    assert published == tmp_path / "versions" / "200"
    assert read_current_pointer(tmp_path) == 200


def test_pointer_is_atomic_json(tmp_path):
    write_current_pointer(tmp_path, 123)

    assert json.loads((tmp_path / "current_version.json").read_text(encoding="utf-8")) == {"version": 123}
