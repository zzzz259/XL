"""增量立绘后处理：基线、增量、继承、失败恢复、事件与关闭默认。"""

from __future__ import annotations

import hashlib
import json
import struct
import zlib
from dataclasses import replace
from datetime import datetime, timezone
from pathlib import Path

import pytest

from server_app import portrait_update
from server_app.cdn import CategoryInfo, CdnClient
from server_app.catalog import AssetRef, BundleRef, CatalogIndex
from server_app.config import ServerConfig
from server_app.processor import ProductionUpdateProcessor


BASE_FASHION = """return {
  [12001513] = { id = 12001513, card_id = 10000151, show_spine = "cardspine_10051_3", show_spine_position = { 0, 0 } },
  [12001514] = { id = 12001514, card_id = 10000151, show_spine = "cardspine_10051_4", show_spine_position = { 10, 0 } },
  [12001515] = { id = 12001515, card_id = 10000224, show_spine = "cardspine_10060_1" },
  [10000001] = { id = 10000001, card_id = 10000999 },
}
"""

# 12001514 的 show_spine_position 从 { 10, 0 } 变为 { 20, 0 }
BASE_FASHION_POS_CHANGED = BASE_FASHION.replace("{ 10, 0 }", "{ 20, 0 }")

# 新增角色 10000333 的两套皮肤（游戏顺序排在原有三套之后）
BASE_FASHION_NEW_CHARACTER = BASE_FASHION.replace(
    "  [10000001] = { id = 10000001, card_id = 10000999 },\n",
    "  [10000001] = { id = 10000001, card_id = 10000999 },\n"
    "  [14001520] = { id = 14001520, card_id = 10000333, show_spine = \"cardspine_10070_1\" },\n"
    "  [14001521] = { id = 14001521, card_id = 10000333, show_spine = \"cardspine_10070_2\" },\n",
)

# 老角色新增一套皮肤 14001522
BASE_FASHION_NEW_SKIN = BASE_FASHION_NEW_CHARACTER.replace(
    "  [14001521] = { id = 14001521, card_id = 10000333, show_spine = \"cardspine_10070_2\" },\n",
    "  [14001521] = { id = 14001521, card_id = 10000333, show_spine = \"cardspine_10070_2\" },\n"
    "  [14001522] = { id = 14001522, card_id = 10000333, show_spine = \"cardspine_10070_3\" },\n",
)

# 14001520 的输入变化（prefab 更换）
BASE_FASHION_CHANGED_INPUT = BASE_FASHION_NEW_CHARACTER.replace(
    "cardspine_10070_1", "cardspine_10070_9"
)

CATALOG = CatalogIndex(
    bundles=(
        BundleRef("p3.bundle", "h3", 10, (3,)),
        BundleRef("p4.bundle", "h4", 10, (3,)),
        BundleRef("p60.bundle", "h60", 10, (3,)),
        BundleRef("shared.bundle", "hshared", 10, ()),
    ),
    assets=(
        AssetRef("cardspine_10051_3.prefab", 0, "Assets/Arts/CardSpine"),
        AssetRef("cardspine_10051_4.prefab", 1, "Assets/Arts/CardSpine"),
        AssetRef("cardspine_10060_1.prefab", 2, "Assets/Arts/CardSpine"),
    ),
)

# cardspine_10051_3 所在 bundle 内容变化（h3 -> h3v2）
CATALOG_BUNDLE_CHANGED = CatalogIndex(
    bundles=(
        BundleRef("p3v2.bundle", "h3v2", 12, (3,)),
        BundleRef("p4.bundle", "h4", 10, (3,)),
        BundleRef("p60.bundle", "h60", 10, (3,)),
        BundleRef("shared.bundle", "hshared", 10, ()),
    ),
    assets=CATALOG.assets,
)

CATALOG_NEW_CHARACTER = CatalogIndex(
    bundles=(
        BundleRef("p3.bundle", "h3", 10, (5,)),
        BundleRef("p4.bundle", "h4", 10, (5,)),
        BundleRef("p60.bundle", "h60", 10, (5,)),
        BundleRef("p70a.bundle", "h70a", 10, (5,)),
        BundleRef("p70b.bundle", "h70b", 10, (5,)),
        BundleRef("shared.bundle", "hshared", 10, ()),
    ),
    assets=(
        AssetRef("cardspine_10051_3.prefab", 0, "Assets/Arts/CardSpine"),
        AssetRef("cardspine_10051_4.prefab", 1, "Assets/Arts/CardSpine"),
        AssetRef("cardspine_10060_1.prefab", 2, "Assets/Arts/CardSpine"),
        AssetRef("cardspine_10070_1.prefab", 3, "Assets/Arts/CardSpine"),
        AssetRef("cardspine_10070_2.prefab", 4, "Assets/Arts/CardSpine"),
    ),
)

CATALOG_NEW_SKIN = CatalogIndex(
    bundles=(
        *CATALOG_NEW_CHARACTER.bundles,
        BundleRef("p70c.bundle", "h70c", 10, (5,)),
    ),
    assets=(
        *CATALOG_NEW_CHARACTER.assets,
        AssetRef("cardspine_10070_3.prefab", 5, "Assets/Arts/CardSpine"),
    ),
)

CATALOG_CHANGED_INPUT = CatalogIndex(
    bundles=CATALOG_NEW_CHARACTER.bundles,
    assets=(
        AssetRef("cardspine_10051_3.prefab", 0, "Assets/Arts/CardSpine"),
        AssetRef("cardspine_10051_4.prefab", 1, "Assets/Arts/CardSpine"),
        AssetRef("cardspine_10060_1.prefab", 2, "Assets/Arts/CardSpine"),
        AssetRef("cardspine_10070_9.prefab", 3, "Assets/Arts/CardSpine"),
        AssetRef("cardspine_10070_2.prefab", 4, "Assets/Arts/CardSpine"),
    ),
)

CHARACTERS = {
    "10000151": {"name": "篝火/测试"},
    "10000224": {"name": "雾铃"},
    "10000333": {"name": "新角色"},
}


def _png_bytes(width: int, height: int, seed: int = 0) -> bytes:
    def chunk(tag: bytes, payload: bytes) -> bytes:
        return (
            struct.pack(">I", len(payload))
            + tag
            + payload
            + struct.pack(">I", zlib.crc32(tag + payload) & 0xFFFFFFFF)
        )

    ihdr = struct.pack(">IIBBBBB", width, height, 8, 6, 0, 0, 0)
    row = b"\x00" + bytes([seed, 8, 16, 255]) * width
    return (
        b"\x89PNG\r\n\x1a\n"
        + chunk(b"IHDR", ihdr)
        + chunk(b"IDAT", zlib.compress(row * height))
        + chunk(b"IEND", b"")
    )


def _config(data_dir: Path, tmp: Path, *, enabled: bool = True) -> ServerConfig:
    fields: dict = {}
    if enabled:
        tools = tmp / "tools"
        (tools / "dotnet").mkdir(parents=True, exist_ok=True)
        (tools / "dotnet" / "dotnet").write_bytes(b"dotnet")
        (tools / "scene").mkdir(parents=True, exist_ok=True)
        (tools / "scene" / "PortraitSceneCLI.dll").write_bytes(b"cli")
        (tools / "as").mkdir(parents=True, exist_ok=True)
        (tools / "as" / "AssetStudioCLI").write_bytes(b"as")
        fields = dict(
            portrait_enabled=True,
            portrait_dotnet_path=tools / "dotnet" / "dotnet",
            portrait_scene_cli_path=tools / "scene" / "PortraitSceneCLI.dll",
            portrait_assetstudio_cli=tools / "as" / "AssetStudioCLI",
        )
    return ServerConfig(
        config_path=data_dir / "config.toml",
        data_dir=data_dir,
        cdn_base="https://example.com",
        timezone_name="Asia/Shanghai",
        normal_interval_seconds=3600,
        burst_interval_seconds=60,
        burst_duration_seconds=1200,
        burst_anchor=datetime(2026, 9, 18, 10, 0, tzinfo=timezone.utc),
        selected_categories=("Arts", "Data"),
        java_bin="java",
        unluac_jar=data_dir / "unluac.jar",
        unluac_opmap=data_dir / "opmap",
        character_card_font=None,
        node_bin="node",
        assetbundle_key=b"yunguihaowan1234",
        render_cards=False,
        **fields,
    )


def _install_runner_fakes(monkeypatch, calls: dict, failures: set[str]) -> None:
    def fake_download(_runner, bundle_hash, destination):
        destination = Path(destination)
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_bytes(f"bundle-{bundle_hash}".encode())
        return destination

    def fake_map(_runner, bundle_dir, map_path):
        map_path = Path(map_path)
        map_path.parent.mkdir(parents=True, exist_ok=True)
        map_path.write_text(json.dumps({"version_timestamp": 1, "assets": []}), encoding="utf-8")

    def fake_render(_runner, task, workspace):
        calls.setdefault("rendered", []).append(task.skin_id)
        if task.skin_id in failures:
            return {"schema_version": 2, "job_id": "job", "status": "error",
                    "diagnostics": ["渲染失败(测试)"]}
        png = _png_bytes(30 + task.ordinal, 22, seed=task.ordinal)
        output = Path(workspace) / "output"
        output.mkdir(parents=True, exist_ok=True)
        artifact = output / f"{task.character_id}_{task.skin_id}.png"
        artifact.write_bytes(png)
        return {
            "schema_version": 2,
            "job_id": "job",
            "status": "success",
            "artifact_path": str(artifact),
            "width": 30 + task.ordinal,
            "height": 22,
            "sha256": hashlib.sha256(png).hexdigest(),
        }

    monkeypatch.setattr(portrait_update.PortraitUpdateRunner, "_download_bundle", fake_download)
    monkeypatch.setattr(portrait_update.PortraitUpdateRunner, "_build_assets_map", fake_map)
    monkeypatch.setattr(portrait_update.PortraitUpdateRunner, "_run_render_job", fake_render)


class PortraitEnv:
    """模拟 updater 的一次/多次版本运行：staging、发布、事件流。"""

    def __init__(self, tmp_path: Path, monkeypatch, *, enabled: bool = True):
        self.tmp = tmp_path
        self.monkeypatch = monkeypatch
        self.data_dir = tmp_path / "data"
        self.calls: dict = {"rendered": []}
        self.config = _config(self.data_dir, tmp_path, enabled=enabled)
        self.client = CdnClient("https://example.com")
        self.run_index = 0

        def fake_download_category_catalog(_client, _category, _hash, destination):
            destination = Path(destination)
            destination.parent.mkdir(parents=True, exist_ok=True)
            destination.write_bytes(b"catalog")
            return destination

        monkeypatch.setattr(CdnClient, "download_category_catalog", fake_download_category_catalog)

    def run(self, version: int, *, basefashion: str = BASE_FASHION,
            catalog: CatalogIndex = CATALOG, characters=None, failures: set[str] = frozenset()):
        self.run_index += 1
        decoded = self.tmp / f"decoded-{self.run_index}"
        decoded.mkdir(parents=True)
        (decoded / "BaseFashion.lua").write_text(basefashion, encoding="utf-8")
        staging = self.tmp / f"staging-{self.run_index}"
        staging.mkdir()
        self.monkeypatch.setattr(portrait_update, "read_catalog", lambda _path, _key: catalog)
        _install_runner_fakes(self.monkeypatch, self.calls, failures)
        summary = portrait_update.run_portrait_update(
            config=self.config,
            client=self.client,
            catalog_dir=self.data_dir / "catalogs",
            version=version,
            decoded_lua=decoded,
            staging=staging,
            characters=characters if characters is not None else CHARACTERS,
            arts_category=CategoryInfo(name="Arts", hash="artshash", size=1, version=1),
        )
        return summary, staging

    def publish(self, staging: Path, version: int) -> Path:
        root = self.data_dir / "versions" / str(version)
        root.mkdir(parents=True)
        (staging / "portraits").rename(root / "portraits")
        return root

    def manifest(self, staging: Path) -> dict:
        return json.loads((staging / "portraits" / "manifest.json").read_text(encoding="utf-8"))

    def published_manifest(self, version: int) -> dict:
        path = self.data_dir / "versions" / str(version) / "portraits" / "manifest.json"
        return json.loads(path.read_text(encoding="utf-8"))

    def events(self) -> list[dict]:
        log = self.data_dir / "update_events.jsonl"
        if not log.is_file():
            return []
        return [json.loads(line) for line in log.read_text(encoding="utf-8").splitlines() if line.strip()]

    def ready_events(self, version: int) -> list[dict]:
        return [e for e in self.events() if e.get("event") == "portrait_ready" and e.get("version") == str(version)]


def _skins_by_id(manifest: dict) -> dict[str, dict]:
    return {entry["skin_id"]: entry for entry in manifest["skins"]}


def _run_baseline(env: PortraitEnv) -> None:
    """首次启用：建立基线（只记录指纹，不渲染），并发布该版本。"""
    _, staging = env.run(1)
    env.publish(staging, 1)


# ---- 首次基线 ---------------------------------------------------------------

def test_first_run_records_fingerprints_without_rendering(tmp_path, monkeypatch):
    env = PortraitEnv(tmp_path, monkeypatch)
    summary, staging = env.run(9)

    assert env.calls["rendered"] == []
    assert summary.success == 0 and summary.failed == 0 and summary.skipped == 3
    manifest = env.manifest(staging)
    assert manifest["schema_version"] == 1
    assert manifest["version"] == "9"
    skins = _skins_by_id(manifest)
    # 无 show_spine 的条目被排除；每个角色按游戏顺序从 1 独立编号
    assert set(skins) == {"12001513", "12001514", "12001515"}
    assert [entry["ordinal"] for entry in manifest["skins"]] == [1, 2, 1]
    assert skins["12001513"]["character_id"] == "10000151"
    assert skins["12001515"]["character_id"] == "10000224"
    for entry in manifest["skins"]:
        assert entry["state"] == "not_generated"
        assert entry["input_fingerprint"]
        assert entry["artifact_path"] is None
        assert entry["error"] is None
    # 首跑不渲染、不发 portrait_ready、不写 outbox
    assert env.events() == []
    assert not (env.data_dir / "portrait_outbox" / "9").exists()


def test_unchanged_skins_stay_not_generated_after_baseline(tmp_path, monkeypatch):
    env = PortraitEnv(tmp_path, monkeypatch)
    _run_baseline(env)

    summary, staging = env.run(2)
    # 指纹未变且无历史产物：不补历史、不重渲
    assert env.calls["rendered"] == []
    assert summary.success == 0 and summary.skipped == 3
    skins = _skins_by_id(env.manifest(staging))
    assert all(entry["state"] == "not_generated" for entry in skins.values())
    assert env.ready_events(2) == []


# ---- 增量渲染 ---------------------------------------------------------------

def test_new_character_skins_render_with_full_contract(tmp_path, monkeypatch):
    env = PortraitEnv(tmp_path, monkeypatch)
    _run_baseline(env)

    summary, staging = env.run(2, basefashion=BASE_FASHION_NEW_CHARACTER, catalog=CATALOG_NEW_CHARACTER)
    assert env.calls["rendered"] == ["14001520", "14001521"]
    assert summary.success == 2 and summary.failed == 0 and summary.skipped == 3

    manifest = env.manifest(staging)
    skins = _skins_by_id(manifest)
    assert [entry["ordinal"] for entry in manifest["skins"]] == [1, 2, 1, 1, 2]
    rendered = {sid: skins[sid] for sid in ("14001520", "14001521")}
    assert all(entry["state"] == "published" for entry in rendered.values())
    assert rendered["14001520"]["character_id"] == "10000333"
    assert rendered["14001520"]["name"] == "新角色"
    assert rendered["14001520"]["ordinal"] == 1  # 每个角色独立编号，不按全局序号
    assert rendered["14001521"]["ordinal"] == 2
    for entry in rendered.values():
        assert entry["artifact_sha256"]
        assert (staging / "portraits" / entry["artifact_path"]).is_file()
        assert entry["source_version"] == 2

    events = env.ready_events(2)
    assert [e["skin_id"] for e in events] == ["14001520", "14001521"]
    for event in events:
        entry = skins[event["skin_id"]]
        assert event["character_id"] == entry["character_id"]
        assert event["ordinal"] == entry["ordinal"]
        assert event["name"] == entry["name"]
        assert event["sha256"] == entry["artifact_sha256"]
        assert event["width"] == entry["width"] and event["height"] == entry["height"]
        assert event["path"].startswith("portrait_outbox/2/")
        assert event["path"].endswith(".png")
        assert datetime.fromisoformat(event["at"]).tzinfo is not None
        outbox_file = env.data_dir / event["path"]
        assert outbox_file.is_file()
        assert outbox_file.name == f"{event['character_id']}_{event['skin_id']}_{event['sha256'][:12]}.png"
        assert hashlib.sha256(outbox_file.read_bytes()).hexdigest() == event["sha256"]


def test_new_skin_on_existing_character_and_inherit(tmp_path, monkeypatch):
    env = PortraitEnv(tmp_path, monkeypatch)
    _run_baseline(env)
    summary2, staging2 = env.run(2, basefashion=BASE_FASHION_NEW_CHARACTER, catalog=CATALOG_NEW_CHARACTER)
    env.publish(staging2, 2)

    env.calls["rendered"].clear()
    summary3, staging3 = env.run(3, basefashion=BASE_FASHION_NEW_SKIN, catalog=CATALOG_NEW_SKIN)
    # 只有老角色新增皮肤 14001522 渲染；14001520/14001521 未变化直接继承
    assert env.calls["rendered"] == ["14001522"]
    assert summary3.success == 1 and summary3.failed == 0 and summary3.skipped == 5

    manifest = env.manifest(staging3)
    skins = _skins_by_id(manifest)
    assert [entry["ordinal"] for entry in manifest["skins"]] == [1, 2, 1, 1, 2, 3]
    assert skins["14001522"]["state"] == "published" and skins["14001522"]["source_version"] == 3
    assert skins["14001522"]["ordinal"] == 3  # 新角色第三套，仍从角色自身计数
    prev_skins = _skins_by_id(env.published_manifest(2))
    for skin_id in ("14001520", "14001521"):
        assert skins[skin_id]["state"] == "published"
        assert skins[skin_id]["source_version"] == 2  # 继承旧产物
        assert skins[skin_id]["artifact_sha256"] == prev_skins[skin_id]["artifact_sha256"]
        assert (staging3 / "portraits" / skins[skin_id]["artifact_path"]).is_file()
    # 基线时代的老皮肤依旧不补历史
    assert skins["12001513"]["state"] == "not_generated"
    # 继承不重复发事件
    assert [e["skin_id"] for e in env.ready_events(3)] == ["14001522"]


def test_lua_position_change_rerenders_only_changed_skin(tmp_path, monkeypatch):
    env = PortraitEnv(tmp_path, monkeypatch)
    _run_baseline(env)

    summary, staging = env.run(2, basefashion=BASE_FASHION_POS_CHANGED)
    assert env.calls["rendered"] == ["12001514"]
    assert summary.success == 1 and summary.skipped == 2
    skins = _skins_by_id(env.manifest(staging))
    assert skins["12001514"]["source_version"] == 2
    assert skins["12001513"]["state"] == "not_generated"


def test_bundle_hash_change_counts_as_input_change(tmp_path, monkeypatch):
    env = PortraitEnv(tmp_path, monkeypatch)
    _run_baseline(env)
    baseline = env.published_manifest(1)

    summary, staging2 = env.run(2, catalog=CATALOG_BUNDLE_CHANGED)
    assert env.calls["rendered"] == ["12001513"]
    assert summary.success == 1
    skins = _skins_by_id(env.manifest(staging2))
    baseline_skins = _skins_by_id(baseline)
    assert skins["12001513"]["input_fingerprint"] != baseline_skins["12001513"]["input_fingerprint"]
    assert skins["12001514"]["input_fingerprint"] == baseline_skins["12001514"]["input_fingerprint"]


# ---- 失败与恢复 ---------------------------------------------------------------

def test_single_skin_failure_continues_and_ordinals_stable(tmp_path, monkeypatch):
    env = PortraitEnv(tmp_path, monkeypatch)
    _run_baseline(env)

    summary, staging = env.run(2, basefashion=BASE_FASHION_NEW_CHARACTER,
                               catalog=CATALOG_NEW_CHARACTER, failures={"14001520"})
    assert env.calls["rendered"] == ["14001520", "14001521"]
    assert summary.success == 1 and summary.failed == 1 and summary.skipped == 3
    manifest = env.manifest(staging)
    skins = _skins_by_id(manifest)
    assert skins["14001520"]["state"] == "failed"
    assert skins["14001520"]["error"]
    assert [entry["ordinal"] for entry in manifest["skins"]] == [1, 2, 1, 1, 2]
    assert skins["14001521"]["state"] == "published"
    # 失败皮肤不发事件；成功的一套发事件
    assert [e["skin_id"] for e in env.ready_events(2)] == ["14001521"]


def test_failed_skin_with_previous_artifact_is_kept_stale(tmp_path, monkeypatch):
    env = PortraitEnv(tmp_path, monkeypatch)
    _run_baseline(env)
    _, staging2 = env.run(2, basefashion=BASE_FASHION_NEW_CHARACTER, catalog=CATALOG_NEW_CHARACTER)
    env.publish(staging2, 2)
    prev_sha = _skins_by_id(env.published_manifest(2))["14001520"]["artifact_sha256"]

    env.calls["rendered"].clear()
    _, staging3 = env.run(3, basefashion=BASE_FASHION_CHANGED_INPUT,
                          catalog=CATALOG_CHANGED_INPUT, failures={"14001520"})
    assert env.calls["rendered"] == ["14001520"]
    skins = _skins_by_id(env.manifest(staging3))
    stale = skins["14001520"]
    assert stale["state"] == "stale"
    assert stale["artifact_sha256"] == prev_sha  # 旧图保留
    assert stale["source_version"] == 2
    assert stale["error"]
    kept = staging3 / "portraits" / stale["artifact_path"]
    assert kept.is_file()
    assert hashlib.sha256(kept.read_bytes()).hexdigest() == prev_sha
    # 未变化皮肤继承；失败/旧图不触发 portrait_ready
    assert skins["14001521"]["state"] == "published"
    assert env.ready_events(3) == []


def test_same_version_resume_completes_only_unfinished(tmp_path, monkeypatch):
    env = PortraitEnv(tmp_path, monkeypatch)
    _run_baseline(env)

    summary1, _ = env.run(2, basefashion=BASE_FASHION_NEW_CHARACTER,
                          catalog=CATALOG_NEW_CHARACTER, failures={"14001520"})
    assert sorted(env.calls["rendered"]) == ["14001520", "14001521"]
    assert summary1.failed == 1
    # 第一版未发布（模拟处理中断后 staging 被清理）：事件只有成功一套
    assert [e["skin_id"] for e in env.ready_events(2)] == ["14001521"]

    env.calls["rendered"].clear()
    summary2, staging2 = env.run(2, basefashion=BASE_FASHION_NEW_CHARACTER,
                                 catalog=CATALOG_NEW_CHARACTER)
    # 只恢复失败项；已成功项不重复渲染
    assert env.calls["rendered"] == ["14001520"]
    assert summary2.success == 1 and summary2.failed == 0 and summary2.skipped == 4
    ready = env.ready_events(2)
    assert [e["skin_id"] for e in ready] == ["14001521", "14001520"]
    for skin_id in ("14001520", "14001521"):
        assert len([e for e in ready if e["skin_id"] == skin_id]) == 1
    manifest = env.manifest(staging2)
    assert [entry["ordinal"] for entry in manifest["skins"]] == [1, 2, 1, 1, 2]
    skins = _skins_by_id(manifest)
    assert skins["14001520"]["state"] == "published"
    assert skins["14001521"]["state"] == "published"
    for skin_id in ("14001520", "14001521"):
        assert (staging2 / "portraits" / skins[skin_id]["artifact_path"]).is_file()


def test_resume_after_publish_does_not_repeat(tmp_path, monkeypatch):
    env = PortraitEnv(tmp_path, monkeypatch)
    _run_baseline(env)
    _, staging = env.run(2, basefashion=BASE_FASHION_NEW_CHARACTER, catalog=CATALOG_NEW_CHARACTER)
    env.publish(staging, 2)
    rendered_after_publish = list(env.calls["rendered"])

    # 同版本再次进入（例如手动重跑且版本指针未推进）也不重复渲染/事件
    summary_repeat, staging_repeat = env.run(2, basefashion=BASE_FASHION_NEW_CHARACTER,
                                             catalog=CATALOG_NEW_CHARACTER)
    assert env.calls["rendered"] == rendered_after_publish
    assert summary_repeat.success == 0 and summary_repeat.skipped == 5
    assert len(env.ready_events(2)) == 2


def test_shared_input_failure_is_clear(tmp_path, monkeypatch):
    env = PortraitEnv(tmp_path, monkeypatch)
    decoded = tmp_path / "decoded"
    decoded.mkdir()
    (decoded / "BaseFashion.lua").write_text(BASE_FASHION, encoding="utf-8")
    staging = tmp_path / "staging"
    staging.mkdir()
    with pytest.raises(portrait_update.PortraitUpdateError, match="Arts"):
        portrait_update.run_portrait_update(
            config=env.config,
            client=env.client,
            catalog_dir=env.data_dir / "catalogs",
            version=2,
            decoded_lua=decoded,
            staging=staging,
            characters=CHARACTERS,
            arts_category=None,
        )


def test_missing_basefashion_is_clear_shared_failure(tmp_path, monkeypatch):
    env = PortraitEnv(tmp_path, monkeypatch)
    decoded = tmp_path / "decoded"
    decoded.mkdir()
    (decoded / "BaseCard.lua").write_text("return {}", encoding="utf-8")
    staging = tmp_path / "staging"
    staging.mkdir()
    with pytest.raises(portrait_update.PortraitUpdateError, match="BaseFashion"):
        portrait_update.run_portrait_update(
            config=env.config,
            client=env.client,
            catalog_dir=env.data_dir / "catalogs",
            version=2,
            decoded_lua=decoded,
            staging=staging,
            characters=CHARACTERS,
            arts_category=CategoryInfo(name="Arts", hash="artshash", size=1, version=1),
        )


def test_missing_tools_mark_tasks_failed_not_faked(tmp_path, monkeypatch):
    env = PortraitEnv(tmp_path, monkeypatch, enabled=False)
    env.config = replace(env.config, portrait_enabled=True)  # 无工具路径
    _run_baseline(env)

    summary, staging = env.run(2, basefashion=BASE_FASHION_NEW_CHARACTER, catalog=CATALOG_NEW_CHARACTER)
    # 工具缺失 → 每个待渲染皮肤明确失败，不伪造成功
    assert env.calls["rendered"] == []
    assert summary.failed == 2 and summary.success == 0 and summary.skipped == 3
    skins = _skins_by_id(env.manifest(staging))
    failed = {sid: skins[sid] for sid in ("14001520", "14001521")}
    assert all(entry["state"] == "failed" and entry["error"] for entry in failed.values())
    assert all("工具" in entry["error"] for entry in failed.values())
    assert env.ready_events(2) == []


def test_event_fields_follow_plan_contract(tmp_path, monkeypatch):
    env = PortraitEnv(tmp_path, monkeypatch)
    _run_baseline(env)
    env.run(2, basefashion=BASE_FASHION_NEW_CHARACTER, catalog=CATALOG_NEW_CHARACTER)
    event = env.ready_events(2)[0]
    assert set(event) == {
        "event", "version", "character_id", "skin_id",
        "ordinal", "name", "path", "sha256", "width", "height", "at",
    }
    assert event["event"] == "portrait_ready"
    assert event["version"] == "2"


def test_render_request_passes_task2_entry_validation(tmp_path, monkeypatch):
    """runner 构造的请求必须通过 Task 2 受控入口的真实校验（防契约漂移）。"""
    import subprocess

    config = _config(tmp_path / "data", tmp_path, enabled=True)
    runner = portrait_update.PortraitUpdateRunner(
        config, CdnClient("https://example.com"), tmp_path / "catalogs"
    )
    runner._version_key = "77"
    runner._lua_store = tmp_path / "lua-store"
    runner._lua_store.mkdir(parents=True)
    (runner._lua_store / "BaseFashion.lua").write_text(BASE_FASHION, encoding="utf-8")
    runner._load_lua_config()  # 真实流程先装载 Tool 解析器（会把 Tool 根加入 sys.path）

    base = tmp_path / "skin-work"
    input_root = base / "workspace"
    (input_root / "lua" / "77").mkdir(parents=True)
    (input_root / "lua" / "77" / "BaseFashion.lua").write_text(BASE_FASHION, encoding="utf-8")
    (input_root / "bundles" / "77").mkdir(parents=True)
    (input_root / "assets_map.json").write_text("[]", encoding="utf-8")
    (base / "output").mkdir(parents=True)
    (base / "work").mkdir(parents=True)
    task = portrait_update._SkinTask(
        skin_id="12001513", character_id="10000151", ordinal=1, name="篝火",
        prefab_name="cardspine_10051_3", bundle_hashes=(), fingerprint="fp", action="render",
    )

    validated: dict = {}

    def fake_run(command, **kwargs):
        from app.features.preview import portrait_headless

        request_path = Path(command[command.index("--request") + 1])
        request = json.loads(request_path.read_text(encoding="utf-8"))
        validated["request"] = portrait_headless.validate_request(request)
        return subprocess.CompletedProcess(command, returncode=1, stderr=b"renderer missing")

    monkeypatch.setattr(subprocess, "run", fake_run)
    with pytest.raises(ValueError, match="未写出结果"):
        runner._run_render_job(task, base)

    request = validated["request"]
    assert request["schema_version"] == 2
    assert request["profile"] == {"format": "png", "scale": 2, "scope": "full"}
    assert request["version"] == "77"
    assert request["character_id"] == "10000151"
    assert request["skin_id"] == "12001513"
    limits = request["limits"]
    assert limits.memory_max_bytes == 512 * 1024 * 1024
    assert limits.memory_high_bytes == 384 * 1024 * 1024
    assert limits.cpu_affinity == (0,)
    assert limits.timeout_seconds == 900
    assert request["png_working_rows"] == 128
    assert Path(request["workspace_root"]).is_dir()
    assert Path(request["output_root"]).is_dir()
    assert Path(request["work_root"]).is_dir()


# ---- AssetStudio map 受控进程 ---------------------------------------------------


def _install_map_process_fake(monkeypatch, captured: dict, outcome: dict) -> None:
    """把 runner 的受控进程入口换成记录型假实现，回放给定运行结果。

    返回真实的 ``RenderProcessLimits``（保留配置值校验），只替换进程执行本身。
    """
    import importlib
    import sys
    from types import SimpleNamespace

    tool_root = portrait_update._tool_app_root()
    if str(tool_root) not in sys.path:
        sys.path.insert(0, str(tool_root))
    module = importlib.import_module("app.platform.portrait_render_process")

    def fake_load(_runner):
        def fake_run(command, *, work_dir, env, limits):
            work_dir = Path(work_dir)
            work_dir.mkdir(parents=True, exist_ok=True)
            captured.update(
                command=[str(part) for part in command],
                work_dir=work_dir,
                env=env,
                limits=limits,
            )
            stderr_path = work_dir / "stderr.log"
            stderr_path.write_text(outcome.get("stderr", ""), encoding="utf-8")
            stdout_path = work_dir / "stdout.log"
            stdout_path.write_text(outcome.get("stdout", ""), encoding="utf-8")
            if outcome.get("write_map"):
                map_path = Path(outcome["write_map"])
                map_path.parent.mkdir(parents=True, exist_ok=True)
                map_path.write_text(
                    json.dumps({
                        "version_timestamp": 1,
                        "assets": [
                            {"name": "cardspine_10051_3.prefab",
                             "path": "assets/arts/cardspine/cardspine_10051_3.prefab", "size": 4096}
                        ],
                    }),
                    encoding="utf-8",
                )
            return SimpleNamespace(
                returncode=outcome.get("returncode", 0),
                timed_out=outcome.get("timed_out", False),
                peak_memory_bytes=None,
                unit=None,
                elapsed_seconds=0.5,
                stdout_path=stdout_path,
                stderr_path=stderr_path,
            )

        return module.RenderProcessLimits, fake_run

    monkeypatch.setattr(
        portrait_update.PortraitUpdateRunner, "_load_render_process", fake_load, raising=False
    )


def _forbid_bare_subprocess(monkeypatch) -> None:
    def forbidden(*_args, **_kwargs):
        raise AssertionError("AssetStudio map 必须走受控进程入口，不得裸 subprocess.run")

    monkeypatch.setattr(portrait_update.subprocess, "run", forbidden)


def test_build_assets_map_uses_controlled_process_with_config_limits(tmp_path, monkeypatch):
    config = _config(tmp_path / "data", tmp_path, enabled=True)
    config = replace(
        config,
        portrait_map_timeout_seconds=321,
        portrait_memory_high_bytes=256 * 1024 * 1024,
        portrait_cpu_affinity=(2,),
    )
    runner = portrait_update.PortraitUpdateRunner(
        config, CdnClient("https://example.com"), tmp_path / "catalogs"
    )
    captured: dict = {}
    map_path = tmp_path / "work-root" / "2" / "pool" / "assets_map.json"
    _install_map_process_fake(monkeypatch, captured, {"write_map": map_path})
    _forbid_bare_subprocess(monkeypatch)

    bundle_dir = map_path.parent / "bundles"
    runner._build_assets_map(bundle_dir, map_path)

    assert captured["command"] == [
        str(config.portrait_assetstudio_cli),
        str(bundle_dir),
        str(map_path.parent),
        "--game", "UnityCN", "--key_index", "23", "--map_op", "Both", "--map_type", "JSON",
    ]
    limits = captured["limits"]
    assert limits.memory_max_bytes == 512 * 1024 * 1024  # 默认 512MiB 上限
    assert limits.memory_high_bytes == 256 * 1024 * 1024
    assert limits.cpu_affinity == (2,)
    assert limits.timeout_seconds == 321  # map 使用 map_timeout_seconds
    assert isinstance(captured["env"], dict)
    assert captured["work_dir"] == map_path.parent / "assetstudio_map"  # 本次 work 内


def test_build_assets_map_wraps_dotnet_runtime(tmp_path, monkeypatch):
    config = _config(tmp_path / "data", tmp_path, enabled=True)
    dotnet = tmp_path / "dotnet" / "dotnet"
    dotnet.parent.mkdir(parents=True)
    dotnet.write_bytes(b"dotnet")
    config = replace(config, portrait_assetstudio_dotnet=dotnet)
    runner = portrait_update.PortraitUpdateRunner(
        config, CdnClient("https://example.com"), tmp_path / "catalogs"
    )
    captured: dict = {}
    map_path = tmp_path / "pool" / "assets_map.json"
    _install_map_process_fake(monkeypatch, captured, {"write_map": map_path})

    runner._build_assets_map(map_path.parent / "bundles", map_path)

    assert captured["command"][0] == str(dotnet)
    assert captured["command"][1] == str(config.portrait_assetstudio_cli)


def test_build_assets_map_requires_cli(tmp_path, monkeypatch):
    config = replace(_config(tmp_path / "data", tmp_path, enabled=True), portrait_assetstudio_cli=None)
    runner = portrait_update.PortraitUpdateRunner(
        config, CdnClient("https://example.com"), tmp_path / "catalogs"
    )
    with pytest.raises(portrait_update.PortraitUpdateError, match="assetstudio_cli"):
        runner._build_assets_map(tmp_path / "bundles", tmp_path / "assets_map.json")


def test_build_assets_map_timeout_is_clear_error(tmp_path, monkeypatch):
    config = replace(_config(tmp_path / "data", tmp_path, enabled=True), portrait_map_timeout_seconds=7)
    runner = portrait_update.PortraitUpdateRunner(
        config, CdnClient("https://example.com"), tmp_path / "catalogs"
    )
    captured: dict = {}
    map_path = tmp_path / "pool" / "assets_map.json"
    _install_map_process_fake(monkeypatch, captured, {"timed_out": True, "returncode": None,
                                                      "stderr": "stuck halfway"})
    _forbid_bare_subprocess(monkeypatch)

    with pytest.raises(portrait_update.PortraitUpdateError) as excinfo:
        runner._build_assets_map(map_path.parent / "bundles", map_path)

    message = str(excinfo.value)
    assert "超时" in message and "7" in message and "stuck halfway" in message


def test_build_assets_map_nonzero_exit_reports_stderr(tmp_path, monkeypatch):
    config = _config(tmp_path / "data", tmp_path, enabled=True)
    runner = portrait_update.PortraitUpdateRunner(
        config, CdnClient("https://example.com"), tmp_path / "catalogs"
    )
    captured: dict = {}
    map_path = tmp_path / "pool" / "assets_map.json"
    _install_map_process_fake(monkeypatch, captured, {"returncode": 5, "stderr": "boom: bad bundle"})
    _forbid_bare_subprocess(monkeypatch)

    with pytest.raises(portrait_update.PortraitUpdateError) as excinfo:
        runner._build_assets_map(map_path.parent / "bundles", map_path)

    message = str(excinfo.value)
    assert "exit 5" in message and "boom" in message


def test_build_assets_map_missing_output_is_clear_error(tmp_path, monkeypatch):
    config = _config(tmp_path / "data", tmp_path, enabled=True)
    runner = portrait_update.PortraitUpdateRunner(
        config, CdnClient("https://example.com"), tmp_path / "catalogs"
    )
    captured: dict = {}
    map_path = tmp_path / "pool" / "assets_map.json"
    _install_map_process_fake(monkeypatch, captured, {"returncode": 0})  # CLI 未写出 map
    _forbid_bare_subprocess(monkeypatch)

    with pytest.raises(portrait_update.PortraitUpdateError, match="输出为空"):
        runner._build_assets_map(map_path.parent / "bundles", map_path)


# ---- processor / pipeline 集成 ------------------------------------------------


def _patch_processor_env(processor: ProductionUpdateProcessor, monkeypatch, characters,
                         timestamp: int, basefashion: str):
    from server_app.cdn import UpdateInfo, VersionCatalog

    monkeypatch.setattr(
        processor.client, "fetch_update_info", lambda: UpdateInfo(timestamp=timestamp, file="v.json")
    )
    monkeypatch.setattr(
        processor.client,
        "fetch_version_catalog",
        lambda _file: VersionCatalog(
            timestamp=timestamp,
            categories=(
                CategoryInfo(name="Arts", hash="artshash", size=10, version=1),
                CategoryInfo(name="Data", hash="datahash", size=10, version=1),
            ),
        ),
    )
    monkeypatch.setattr(
        processor.client,
        "download_category_catalog",
        lambda *args, **kwargs: Path("catalog"),
    )
    monkeypatch.setattr(processor.client, "download_bundle", lambda hash, dest: Path(dest))

    from server_app.catalog import CatalogIndex as ServerCatalogIndex

    lua_catalog = ServerCatalogIndex(
        bundles=(BundleRef(name="lua.bundle", hash="hash", size=1, deps=()),),
        assets=(AssetRef(name="BaseCard.lua.bytes", bundle_index=0, container="Assets/Lua"),),
    )
    monkeypatch.setattr("server_app.processor.read_catalog", lambda _path, _key: lua_catalog)
    monkeypatch.setattr("server_app.processor.extract_lua_files", lambda _paths, _out: None)

    def fake_decode(_raw, out, *args, **kwargs):
        out = Path(out)
        out.mkdir(parents=True, exist_ok=True)
        (out / "BaseFashion.lua").write_text(basefashion, encoding="utf-8")
        return type("R", (), {"success": 2, "failed": 0})()

    monkeypatch.setattr("server_app.processor.decode_lua_directory", fake_decode)
    monkeypatch.setattr(
        "server_app.processor.parse_character_snapshot", lambda _dir: {"characters": characters}
    )


def _install_processor_runner_fakes(monkeypatch, calls: dict, catalog: CatalogIndex,
                                    failures=frozenset()) -> None:
    monkeypatch.setattr(portrait_update, "read_catalog", lambda _p, _k: catalog)

    def fake_download(_runner, bundle_hash, destination):
        destination = Path(destination)
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_bytes(f"bundle-{bundle_hash}".encode())
        return destination

    def fake_map(_runner, bundle_dir, map_path):
        map_path = Path(map_path)
        map_path.parent.mkdir(parents=True, exist_ok=True)
        map_path.write_text(json.dumps({"assets": []}), encoding="utf-8")

    def fake_render(_runner, task, workspace):
        calls.setdefault("rendered", []).append(task.skin_id)
        if task.skin_id in failures:
            return {"schema_version": 2, "job_id": "job", "status": "error",
                    "diagnostics": ["渲染失败(测试)"]}
        png = _png_bytes(30 + task.ordinal, 22, seed=task.ordinal)
        output = Path(workspace) / "output"
        output.mkdir(parents=True, exist_ok=True)
        artifact = output / f"{task.character_id}_{task.skin_id}.png"
        artifact.write_bytes(png)
        return {
            "schema_version": 2, "job_id": "job", "status": "success",
            "artifact_path": str(artifact), "width": 30 + task.ordinal, "height": 22,
            "sha256": hashlib.sha256(png).hexdigest(),
        }

    monkeypatch.setattr(portrait_update.PortraitUpdateRunner, "_download_bundle", fake_download)
    monkeypatch.setattr(portrait_update.PortraitUpdateRunner, "_build_assets_map", fake_map)
    monkeypatch.setattr(portrait_update.PortraitUpdateRunner, "_run_render_job", fake_render)


def _run_pipeline_version(monkeypatch, data_dir: Path, tmp_path: Path, calls: dict,
                          timestamp: int, basefashion: str, catalog: CatalogIndex):
    """完整走一遍 UpdatePipeline（含原子发布），返回该版本结果。"""
    from server_app.pipeline import UpdatePipeline

    processor = ProductionUpdateProcessor(_config(data_dir, tmp_path, enabled=True))
    _patch_processor_env(processor, monkeypatch, CHARACTERS, timestamp, basefashion)
    _install_processor_runner_fakes(monkeypatch, calls, catalog)
    return UpdatePipeline(data_dir, processor=processor).run_once()


def _read_events(data_dir: Path) -> list[dict]:
    log = data_dir / "update_events.jsonl"
    if not log.is_file():
        return []
    return [json.loads(line) for line in log.read_text(encoding="utf-8").splitlines() if line.strip()]


def test_processor_runs_portraits_between_start_and_finish(tmp_path, monkeypatch):
    from server_app.pipeline import UpdatePipeline

    data_dir = tmp_path / "data"
    data_dir.mkdir()
    calls: dict = {}
    # v1 首次启用：真实 runner 建立基线（not_generated），经 pipeline 原子发布
    baseline = _run_pipeline_version(monkeypatch, data_dir, tmp_path, calls, 1, BASE_FASHION, CATALOG)
    assert baseline.processed is True
    assert calls.get("rendered", []) == []

    processor = ProductionUpdateProcessor(_config(data_dir, tmp_path, enabled=True))
    _patch_processor_env(processor, monkeypatch, CHARACTERS, 2, BASE_FASHION_NEW_CHARACTER)
    _install_processor_runner_fakes(monkeypatch, calls, CATALOG_NEW_CHARACTER)

    result = UpdatePipeline(data_dir, processor=processor).run_once()
    assert result.processed is True
    assert calls["rendered"] == ["14001520", "14001521"]
    assert result.portrait_success_count == 2
    assert result.portrait_failure_count == 0
    assert result.portrait_skip_count == 3
    events = _read_events(data_dir)
    # v2 的 portrait_ready 事件夹在 v2 的 start/finish 之间
    assert [e["event"] for e in events] == [
        "start", "finish",
        "start", "portrait_ready", "portrait_ready", "finish",
    ]
    finish = events[-1]
    assert finish["portraits"] == {"success": 2, "failed": 0, "skipped": 3}
    # 立绘 manifest 与产物随版本发布
    published = data_dir / "versions" / "2" / "portraits"
    assert (published / "manifest.json").is_file()


def test_processor_portraits_disabled_keeps_legacy_flow(tmp_path, monkeypatch):
    data_dir = tmp_path / "data"
    data_dir.mkdir()
    processor = ProductionUpdateProcessor(_config(data_dir, tmp_path, enabled=False))
    _patch_processor_env(processor, monkeypatch, CHARACTERS, 2, BASE_FASHION_NEW_CHARACTER)

    def forbidden(**_kwargs):
        raise AssertionError("portraits disabled 时不得调用立绘后处理")

    monkeypatch.setattr("server_app.processor.run_portrait_update", forbidden)
    result = processor(tmp_path / "staging")
    assert result.processed is True
    assert (tmp_path / "staging" / "portraits").exists() is False
    events = _read_events(data_dir)
    assert [e["event"] for e in events] == ["start", "finish"]
    assert "portraits" not in events[-1]


def test_processor_portrait_shared_failure_does_not_fail_update(tmp_path, monkeypatch):
    data_dir = tmp_path / "data"
    data_dir.mkdir()
    processor = ProductionUpdateProcessor(_config(data_dir, tmp_path, enabled=True))
    _patch_processor_env(processor, monkeypatch, CHARACTERS, 2, BASE_FASHION_NEW_CHARACTER)

    def boom(**_kwargs):
        raise portrait_update.PortraitUpdateError("arts catalog 读取失败")

    monkeypatch.setattr("server_app.processor.run_portrait_update", boom)
    result = processor(tmp_path / "staging")
    assert result.processed is True
    assert result.error is None
    assert result.portrait_success_count == 0
    assert any("portraits" in warning for warning in result.warnings)
    events = _read_events(data_dir)
    assert [e["event"] for e in events] == ["start", "finish"]
    assert "portraits" not in events[-1]


def test_processor_unexpected_portrait_error_is_contained(tmp_path, monkeypatch):
    data_dir = tmp_path / "data"
    data_dir.mkdir()
    processor = ProductionUpdateProcessor(_config(data_dir, tmp_path, enabled=True))
    _patch_processor_env(processor, monkeypatch, CHARACTERS, 2, BASE_FASHION_NEW_CHARACTER)
    monkeypatch.setattr(
        "server_app.processor.run_portrait_update",
        lambda **_kwargs: (_ for _ in ()).throw(RuntimeError("bug")),
    )
    result = processor(tmp_path / "staging")
    assert result.processed is True
    assert any("portraits" in warning for warning in result.warnings)


def test_pipeline_publishes_portrait_artifacts_with_version(tmp_path, monkeypatch):
    data_dir = tmp_path / "data"
    data_dir.mkdir()
    calls: dict = {}
    baseline = _run_pipeline_version(monkeypatch, data_dir, tmp_path, calls, 1, BASE_FASHION, CATALOG)
    assert baseline.processed is True

    result = _run_pipeline_version(
        monkeypatch, data_dir, tmp_path, calls, 2, BASE_FASHION_NEW_CHARACTER, CATALOG_NEW_CHARACTER
    )
    assert result.processed is True
    published = data_dir / "versions" / "2" / "portraits"
    manifest = json.loads((published / "manifest.json").read_text(encoding="utf-8"))
    assert manifest["version"] == "2"
    assert len(manifest["skins"]) == 5
    published_entries = [entry for entry in manifest["skins"] if entry["state"] == "published"]
    assert len(published_entries) == 2
    for entry in published_entries:
        assert (published / entry["artifact_path"]).is_file()
    # v1 基线 manifest 未被破坏
    v1_manifest = json.loads(
        (data_dir / "versions" / "1" / "portraits" / "manifest.json").read_text(encoding="utf-8")
    )
    assert all(entry["state"] == "not_generated" for entry in v1_manifest["skins"])
    # outbox 持久存在，供 Bot 消费
    assert len(list((data_dir / "portrait_outbox" / "2").glob("*.png"))) == 2
    # 事件顺序：start → 逐套 portrait_ready → finish
    events = _read_events(data_dir)
    assert [e["event"] for e in events] == [
        "start", "finish",
        "start", "portrait_ready", "portrait_ready", "finish",
    ]


# ---- 同版本恢复（processor 真实入口） ------------------------------------------


def test_same_version_with_failed_portraits_resumes_from_processor(tmp_path, monkeypatch):
    """current_version == timestamp 且有失败项时，__call__ 必须真正进入恢复。"""
    from server_app.pipeline import UpdatePipeline

    data_dir = tmp_path / "data"
    data_dir.mkdir()
    calls: dict = {}
    baseline = _run_pipeline_version(monkeypatch, data_dir, tmp_path, calls, 1, BASE_FASHION, CATALOG)
    assert baseline.processed is True
    # 首次正常运行把解码后的 BaseFashion.lua 随版本保存，供同版本恢复使用
    saved_lua_v1 = data_dir / "versions" / "1" / "portraits" / "BaseFashion.lua"
    assert saved_lua_v1.is_file()
    assert saved_lua_v1.read_text(encoding="utf-8") == BASE_FASHION

    calls["rendered"] = []
    processor = ProductionUpdateProcessor(_config(data_dir, tmp_path, enabled=True))
    _patch_processor_env(processor, monkeypatch, CHARACTERS, 2, BASE_FASHION_NEW_CHARACTER)
    _install_processor_runner_fakes(monkeypatch, calls, CATALOG_NEW_CHARACTER, failures={"14001520"})
    result = UpdatePipeline(data_dir, processor=processor).run_once()
    assert result.processed is True
    assert result.portrait_failure_count == 1
    assert calls["rendered"] == ["14001520", "14001521"]

    version_dir = data_dir / "versions" / "2"
    saved_lua_v2 = version_dir / "portraits" / "BaseFashion.lua"
    assert saved_lua_v2.read_text(encoding="utf-8") == BASE_FASHION_NEW_CHARACTER
    core_data = (version_dir / "character_data" / "current.json").read_bytes()
    root_current = (data_dir / "character_data" / "current.json").read_bytes()
    status_before = (data_dir / "update_status.json").read_bytes()
    events_before = _read_events(data_dir)
    assert [e["event"] for e in events_before] == [
        "start", "finish", "start", "portrait_ready", "finish",
    ]

    # 同版本再次进入：角色卡片与图鉴 outbox 绝不允许重跑
    def forbidden(*_args, **_kwargs):
        raise AssertionError("同版本恢复不得重跑角色卡片或图鉴 outbox")

    monkeypatch.setattr("server_app.processor.export_character_cards", forbidden)
    monkeypatch.setattr("server_app.processor._write_outbox", forbidden)

    calls["rendered"] = []
    resume_processor = ProductionUpdateProcessor(_config(data_dir, tmp_path, enabled=True))
    _patch_processor_env(resume_processor, monkeypatch, CHARACTERS, 2, BASE_FASHION_NEW_CHARACTER)
    _install_processor_runner_fakes(monkeypatch, calls, CATALOG_NEW_CHARACTER)
    resume_result = UpdatePipeline(data_dir, processor=resume_processor).run_once()

    assert resume_result.processed is False
    assert resume_result.error is None
    assert resume_result.version_timestamp is None  # 不得触发 coordinator 的 pending 记账
    # 只恢复失败的一套；已成功的一套不重渲
    assert calls["rendered"] == ["14001520"]
    assert resume_result.portrait_success_count == 1
    assert resume_result.portrait_failure_count == 0
    assert resume_result.portrait_skip_count == 4

    events_after = _read_events(data_dir)
    # 不重复 start/finish：新增事件只有恢复成功的一套 portrait_ready，禁止重复
    assert [e["event"] for e in events_after] == [
        *[e["event"] for e in events_before], "portrait_ready",
    ]
    ready = [e for e in events_after if e["event"] == "portrait_ready"]
    assert [e["skin_id"] for e in ready] == ["14001521", "14001520"]
    assert len([e for e in ready if e["skin_id"] == "14001520"]) == 1

    manifest = json.loads((version_dir / "portraits" / "manifest.json").read_text(encoding="utf-8"))
    skins = _skins_by_id(manifest)
    assert skins["14001520"]["state"] == "published"
    assert skins["14001520"]["artifact_path"]
    assert (version_dir / "portraits" / skins["14001520"]["artifact_path"]).is_file()
    # 发布 manifest 继续剥离 work 控制字段
    for entry in skins.values():
        assert "event_emitted" not in entry
        assert "staged" not in entry
    ready_v2 = {e["skin_id"]: e for e in ready}
    assert skins["14001521"]["artifact_sha256"] == ready_v2["14001521"]["sha256"]

    # 角色核心数据、根 current、状态文件均不被同版本恢复修改
    assert (version_dir / "character_data" / "current.json").read_bytes() == core_data
    assert (data_dir / "character_data" / "current.json").read_bytes() == root_current
    assert (data_dir / "update_status.json").read_bytes() == status_before
    # 恢复输入（同版本保存的 Lua）不被改写
    assert saved_lua_v2.read_text(encoding="utf-8") == BASE_FASHION_NEW_CHARACTER
    # outbox 补齐失败的一套，总共两份
    assert len(list((data_dir / "portrait_outbox" / "2").glob("*.png"))) == 2


def test_same_version_without_pending_portraits_touches_nothing(tmp_path, monkeypatch):
    """同版本且无待恢复项：不落 CDN、不改状态、不写任何事件。"""
    data_dir = tmp_path / "data"
    data_dir.mkdir()
    calls: dict = {}
    baseline = _run_pipeline_version(monkeypatch, data_dir, tmp_path, calls, 1, BASE_FASHION, CATALOG)
    assert baseline.processed is True
    result = _run_pipeline_version(
        monkeypatch, data_dir, tmp_path, calls, 2, BASE_FASHION_NEW_CHARACTER, CATALOG_NEW_CHARACTER
    )
    assert result.processed is True
    assert result.portrait_success_count == 2

    rendered_before = list(calls.get("rendered", []))
    events_before = _read_events(data_dir)
    status_before = (data_dir / "update_status.json").read_bytes()
    manifest_before = (data_dir / "versions" / "2" / "portraits" / "manifest.json").read_bytes()

    probe = {"catalog_fetches": 0}

    def counting_fetch_catalog(_file):
        probe["catalog_fetches"] += 1
        from server_app.cdn import VersionCatalog
        return VersionCatalog(
            timestamp=2,
            categories=(
                CategoryInfo(name="Arts", hash="artshash", size=10, version=1),
                CategoryInfo(name="Data", hash="datahash", size=10, version=1),
            ),
        )

    noop_processor = ProductionUpdateProcessor(_config(data_dir, tmp_path, enabled=True))
    _patch_processor_env(noop_processor, monkeypatch, CHARACTERS, 2, BASE_FASHION_NEW_CHARACTER)
    monkeypatch.setattr(noop_processor.client, "fetch_version_catalog", counting_fetch_catalog)
    _install_processor_runner_fakes(monkeypatch, calls, CATALOG_NEW_CHARACTER)

    from server_app.pipeline import UpdatePipeline
    noop_result = UpdatePipeline(data_dir, processor=noop_processor).run_once()

    assert noop_result.processed is False
    assert noop_result.error is None
    assert noop_result.version_timestamp is None
    assert noop_result.portrait_success_count == 0
    assert calls.get("rendered", []) == rendered_before  # 没有任何新渲染
    assert probe["catalog_fetches"] == 0  # 无待恢复项时连版本清单都不拉取
    assert _read_events(data_dir) == events_before
    assert (data_dir / "update_status.json").read_bytes() == status_before
    assert (data_dir / "versions" / "2" / "portraits" / "manifest.json").read_bytes() == manifest_before


def test_same_version_resume_failure_stays_pending(tmp_path, monkeypatch):
    """恢复再次失败：条目留在 failed，下一轮同版本继续恢复；成功前不重复 portrait_ready。"""
    from server_app.pipeline import UpdatePipeline

    data_dir = tmp_path / "data"
    data_dir.mkdir()
    calls: dict = {}
    _run_pipeline_version(monkeypatch, data_dir, tmp_path, calls, 1, BASE_FASHION, CATALOG)

    calls["rendered"] = []
    processor = ProductionUpdateProcessor(_config(data_dir, tmp_path, enabled=True))
    _patch_processor_env(processor, monkeypatch, CHARACTERS, 2, BASE_FASHION_NEW_CHARACTER)
    _install_processor_runner_fakes(monkeypatch, calls, CATALOG_NEW_CHARACTER, failures={"14001520"})
    result = UpdatePipeline(data_dir, processor=processor).run_once()
    assert result.processed is True and result.portrait_failure_count == 1

    # 第一轮恢复：渲染再次失败 → 留待下轮
    calls["rendered"] = []
    resume1 = _resume_run(monkeypatch, data_dir, tmp_path, calls, failures={"14001520"})
    assert calls["rendered"] == ["14001520"]
    assert resume1.processed is False
    assert resume1.portrait_failure_count == 1 and resume1.portrait_success_count == 0
    assert _read_events(data_dir)[-1]["event"] == "finish"  # 无新增 portrait_ready
    manifest = json.loads(
        (data_dir / "versions" / "2" / "portraits" / "manifest.json").read_text(encoding="utf-8")
    )
    assert _skins_by_id(manifest)["14001520"]["state"] == "failed"

    # 第二轮恢复：渲染成功 → published + 恰好一次 portrait_ready
    calls["rendered"] = []
    resume2 = _resume_run(monkeypatch, data_dir, tmp_path, calls)
    assert calls["rendered"] == ["14001520"]
    assert resume2.portrait_success_count == 1 and resume2.portrait_failure_count == 0
    ready = [e for e in _read_events(data_dir) if e["event"] == "portrait_ready"]
    assert [e["skin_id"] for e in ready] == ["14001521", "14001520"]
    manifest = json.loads(
        (data_dir / "versions" / "2" / "portraits" / "manifest.json").read_text(encoding="utf-8")
    )
    assert _skins_by_id(manifest)["14001520"]["state"] == "published"


def test_interrupted_resume_keeps_published_manifest_and_finishes(tmp_path, monkeypatch):
    """恢复处理第一部分后中断：已发布 manifest 不被截断，下一次恢复继续并最终完整。"""
    from server_app.pipeline import UpdatePipeline

    data_dir = tmp_path / "data"
    data_dir.mkdir()
    calls: dict = {}
    baseline = _run_pipeline_version(monkeypatch, data_dir, tmp_path, calls, 1, BASE_FASHION, CATALOG)
    assert baseline.processed is True

    processor = ProductionUpdateProcessor(_config(data_dir, tmp_path, enabled=True))
    _patch_processor_env(processor, monkeypatch, CHARACTERS, 2, BASE_FASHION_NEW_CHARACTER)
    _install_processor_runner_fakes(monkeypatch, calls, CATALOG_NEW_CHARACTER, failures={"14001520"})
    first = UpdatePipeline(data_dir, processor=processor).run_once()
    assert first.processed is True and first.portrait_failure_count == 1

    version_dir = data_dir / "versions" / "2"
    manifest_path = version_dir / "portraits" / "manifest.json"
    published_before = json.loads(manifest_path.read_text(encoding="utf-8"))
    all_skins = set(_skins_by_id(published_before))
    assert all_skins == {"12001513", "12001514", "12001515", "14001520", "14001521"}
    assert _skins_by_id(published_before)["14001520"]["state"] == "failed"
    assert _skins_by_id(published_before)["14001521"]["state"] == "published"

    # 第一次同版本恢复：逐套写 work manifest，第二套之后模拟进程中断
    runner_cls = portrait_update.PortraitUpdateRunner
    real_persist = getattr(runner_cls, "_persist_work_manifest", None)
    state = {"calls": 0}

    def crashing_persist(self, work_manifest_path, version_key, entries):
        real_persist(self, work_manifest_path, version_key, entries)
        state["calls"] += 1
        if state["calls"] >= 2:
            raise RuntimeError("模拟中断")

    monkeypatch.setattr(runner_cls, "_persist_work_manifest", crashing_persist, raising=False)

    calls["rendered"] = []
    resume_processor = ProductionUpdateProcessor(_config(data_dir, tmp_path, enabled=True))
    _patch_processor_env(resume_processor, monkeypatch, CHARACTERS, 2, BASE_FASHION_NEW_CHARACTER)
    _install_processor_runner_fakes(monkeypatch, calls, CATALOG_NEW_CHARACTER)
    interrupted = UpdatePipeline(data_dir, processor=resume_processor).run_once()
    assert interrupted.processed is False
    # 中断被记为 portraits 警告，不升级为更新失败
    assert any("portraits" in warning for warning in interrupted.warnings)

    # 已发布 manifest 未被截断：仍含全部皮肤，失败状态保留
    published_mid = json.loads(manifest_path.read_text(encoding="utf-8"))
    assert set(_skins_by_id(published_mid)) == all_skins
    assert _skins_by_id(published_mid)["14001520"]["state"] == "failed"
    # work manifest 已被截断为前缀：恢复门控不能只看它
    work_manifest_path = portrait_update.portrait_work_manifest_path(
        _config(data_dir, tmp_path, enabled=True), 2
    )
    work_manifest = json.loads(work_manifest_path.read_text(encoding="utf-8"))
    assert [entry["skin_id"] for entry in work_manifest["skins"]] == ["12001513", "12001514"]

    # 撤掉崩溃注入：下一次恢复继续，只重做失败项，不重渲已完成项，最终完整
    monkeypatch.setattr(runner_cls, "_persist_work_manifest", real_persist)
    calls["rendered"] = []
    finished = _resume_run(monkeypatch, data_dir, tmp_path, calls)
    assert calls["rendered"] == ["14001520"]
    assert finished.processed is False
    assert finished.portrait_success_count == 1 and finished.portrait_failure_count == 0

    final = json.loads(manifest_path.read_text(encoding="utf-8"))
    assert set(_skins_by_id(final)) == all_skins
    final_skins = _skins_by_id(final)
    assert final_skins["14001520"]["state"] == "published"
    assert final_skins["14001521"]["state"] == "published"
    assert (version_dir / "portraits" / final_skins["14001520"]["artifact_path"]).is_file()
    ready = [
        e for e in _read_events(data_dir)
        if e["event"] == "portrait_ready" and e["version"] == "2"
    ]
    assert sorted(e["skin_id"] for e in ready) == ["14001520", "14001521"]


def _resume_run(monkeypatch, data_dir: Path, tmp_path: Path, calls: dict, failures=frozenset()):
    """构造与线上相同的同版本 processor 入口并跑一次 pipeline。"""
    from server_app.pipeline import UpdatePipeline

    processor = ProductionUpdateProcessor(_config(data_dir, tmp_path, enabled=True))
    _patch_processor_env(processor, monkeypatch, CHARACTERS, 2, BASE_FASHION_NEW_CHARACTER)
    _install_processor_runner_fakes(monkeypatch, calls, CATALOG_NEW_CHARACTER, failures=failures)
    return UpdatePipeline(data_dir, processor=processor).run_once()


def test_saved_basefashion_write_and_identical_rerun(tmp_path, monkeypatch):
    """BaseFashion.lua 随版本原子保存；同内容已存在时不重写、不报错。"""
    config = _config(tmp_path / "data", tmp_path, enabled=True)
    runner = portrait_update.PortraitUpdateRunner(
        config, CdnClient("https://example.com"), tmp_path / "catalogs"
    )
    staging_portraits = tmp_path / "staging" / "portraits"
    staging_portraits.mkdir(parents=True)
    source = tmp_path / "decoded" / "BaseFashion.lua"
    source.parent.mkdir(parents=True)
    source.write_text(BASE_FASHION, encoding="utf-8")

    runner._save_basefashion(source, staging_portraits)
    saved = staging_portraits / "BaseFashion.lua"
    assert saved.read_text(encoding="utf-8") == BASE_FASHION
    first_stat = saved.stat().st_mtime_ns

    # 同版本恢复时输入与保存文件逐字节一致：不得报错，也不得无谓重写
    runner._save_basefashion(source, staging_portraits)
    assert saved.read_text(encoding="utf-8") == BASE_FASHION
    assert saved.stat().st_mtime_ns == first_stat

    # 内容不同则原子替换（正常首跑 staging 是全新目录，防御性语义）
    source.write_text(BASE_FASHION_POS_CHANGED, encoding="utf-8")
    runner._save_basefashion(source, staging_portraits)
    assert saved.read_text(encoding="utf-8") == BASE_FASHION_POS_CHANGED
