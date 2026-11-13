"""历史内容回填的控制语义契约测试（实施计划 Task 3）。

目标模块 ``server_app.history_backfill``，公共契约：

- ``HistoryVersion(timestamp, manifest_file, categories, bundle_hashes)``；
  ``read_history_index(db_path) -> tuple[HistoryVersion, ...]`` 以只读方式
  （``file:...?mode=ro``）打开更新工具 DB，读取 ``versions`` / ``bundles`` /
  ``sub_bundles`` 三张表，按 timestamp **旧到新**排序；用 DB 中的
  ``manifest_file`` 与分类包哈希即可构造历史 ``UpdateInfo``/``VersionCatalog``。
  DB 只是回填输入，不是运行时依赖；读取不得产生 journal/wal 等写入副作用。

- ``run_backfill(*, config, client, db_path, work_root, versions=None,
  execute=False) -> BackfillReport``：
  默认只预检（不下载、不渲染、不写任何产物）；
  ``execute=True`` 时按旧到新处理，全部产物先写独立工作根
  ``work_root/versions/<v>/``（角色数据、图鉴长图与 card manifest、
  portraits manifest 与实际立绘），逐版本保存进度；
  最旧版本建立完整图鉴/立绘基线——回填专用 Runner 策略允许首个历史基线
  **真实渲染**，而正常更新的“首次只记录指纹不补历史”默认行为不变；
  后续版本只渲染新增或输入变化的内容，其余继承并保留原 source_version；
  全程显式关闭通知：**不追加** portrait_ready/start/finish 事件、不写
  portrait_outbox、不写 outbox，config.data_dir（生产数据根）零改动；
  重复 ``--version``/整轮重跑可恢复且幂等，已完成的版本不重渲。

- ``publish_backfill(*, work_root, production_data_dir, versions=None)
  -> PublishReport(published, merged, rejected)``：
  只把 work_root 中**已通过完整校验**且生产尚不存在的 ``versions/<v>``
  原子合入生产版本目录；已存在的版本只允许补缺失的派生
  ``character_cards/manifest.json``（计入 merged），绝不覆盖已有图片或
  角色 JSON；未通过校验的版本计入 rejected 且不合入；
  永不修改生产 ``current_version.json``、根 ``character_data/current.json``、
  ``update_events.jsonl``、``outbox/`` 与 ``portrait_outbox/``。

测试全部使用虚构 DB/客户端与最小 PNG fixture，不访问网络。
"""

from __future__ import annotations

import hashlib
import json
import os
import sqlite3
import struct
import zlib
from datetime import UTC, datetime
from pathlib import Path

import pytest

from server_app import portrait_update
from server_app.catalog import AssetRef, BundleRef, CatalogIndex
from server_app.cdn import CategoryInfo, VersionCatalog
from server_app.character_card_exporter import CardExportReport
from server_app.config import ServerConfig
from server_app.lua_decoder import DecodeReport


def _history_backfill():
    """延迟导入待实现模块，让对照用例（现有行为保持不变）现在就能跑绿。"""
    from server_app import history_backfill

    return history_backfill


BASE_FASHION_V1 = """return {
  [12001513] = { id = 12001513, card_id = 10000151, show_spine = "cardspine_10051_3", show_spine_position = { 0, 0 } },
  [12001514] = { id = 12001514, card_id = 10000151, show_spine = "cardspine_10051_4", show_spine_position = { 10, 0 } },
  [12001515] = { id = 12001515, card_id = 10000224, show_spine = "cardspine_10060_1" },
}
"""

# 新增角色 10000333 的两套皮肤（游戏顺序排在原有皮肤之后）
BASE_FASHION_V2 = BASE_FASHION_V1.replace(
    "  [12001515] = { id = 12001515, card_id = 10000224, show_spine = \"cardspine_10060_1\" },\n",
    "  [12001515] = { id = 12001515, card_id = 10000224, show_spine = \"cardspine_10060_1\" },\n"
    "  [14001520] = { id = 14001520, card_id = 10000333, show_spine = \"cardspine_10070_1\" },\n"
    "  [14001521] = { id = 14001521, card_id = 10000333, show_spine = \"cardspine_10070_2\" },\n",
)

DATA_CATALOG = CatalogIndex(
    bundles=(BundleRef("d1.bundle", "hd1", 10, ()), BundleRef("d2.bundle", "hd2", 10, ())),
    assets=(
        AssetRef("basecard.lua.bytes", 0, "Assets/Data/Lua"),
        AssetRef("basefashion.lua.bytes", 1, "Assets/Data/Lua"),
    ),
)

ARTS_CATALOG_V1 = CatalogIndex(
    bundles=(
        BundleRef("p3.bundle", "h3", 10, ()),
        BundleRef("p4.bundle", "h4", 10, ()),
        BundleRef("p60.bundle", "h60", 10, ()),
    ),
    assets=(
        AssetRef("cardspine_10051_3.prefab", 0, "Assets/Arts/CardSpine"),
        AssetRef("cardspine_10051_4.prefab", 1, "Assets/Arts/CardSpine"),
        AssetRef("cardspine_10060_1.prefab", 2, "Assets/Arts/CardSpine"),
    ),
)

ARTS_CATALOG_V2 = CatalogIndex(
    bundles=(
        *ARTS_CATALOG_V1.bundles,
        BundleRef("p70a.bundle", "h70a", 10, ()),
        BundleRef("p70b.bundle", "h70b", 10, ()),
    ),
    assets=(
        *ARTS_CATALOG_V1.assets,
        AssetRef("cardspine_10070_1.prefab", 3, "Assets/Arts/CardSpine"),
        AssetRef("cardspine_10070_2.prefab", 4, "Assets/Arts/CardSpine"),
    ),
)

CHARACTERS_V1 = {"10000151": {"name": "篝火/测试"}, "10000224": {"name": "雾铃"}}
CHARACTERS_V2 = {**CHARACTERS_V1, "10000333": {"name": "新角色"}}

DB_ROWS = [
    {
        "timestamp": 100,
        "manifest_file": "100/Android/versions.json",
        "categories": {"Data": "datahash100", "Arts": "artshash100"},
        "sub_hashes": ["suba100", "subb100"],
    },
    {
        "timestamp": 200,
        "manifest_file": "200/Android/versions.json",
        "categories": {"Data": "datahash200", "Arts": "artshash200"},
        "sub_hashes": ["suba200"],
    },
]

_DB_DDL = """
CREATE TABLE versions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    timestamp INTEGER UNIQUE NOT NULL,
    arts_ver INTEGER,
    data_ver INTEGER,
    other_ver INTEGER,
    video_ver INTEGER,
    apk_version TEXT,
    manifest_file TEXT,
    updateinfo_json TEXT,
    versions_json TEXT,
    is_current INTEGER DEFAULT 0,
    downloaded INTEGER DEFAULT 0,
    created_at TEXT DEFAULT (datetime('now','localtime')),
    notes TEXT
);
CREATE TABLE bundles (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL,
    hash TEXT NOT NULL,
    size INTEGER,
    ver INTEGER,
    version_timestamp INTEGER NOT NULL,
    local_path TEXT,
    UNIQUE(name, ver, version_timestamp)
);
CREATE TABLE sub_bundles (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    hash TEXT NOT NULL,
    category TEXT,
    version_timestamp INTEGER NOT NULL,
    size INTEGER,
    downloadable INTEGER DEFAULT 0,
    local_path TEXT,
    added_in_version INTEGER,
    removed_in_version INTEGER,
    UNIQUE(hash, version_timestamp)
);
"""


def _create_db(path: Path, rows: list[dict]) -> Path:
    conn = sqlite3.connect(path)
    conn.executescript(_DB_DDL)
    # 故意按新到旧插入，排序必须来自 timestamp 而非插入顺序/自增 id
    for row in sorted(rows, key=lambda item: item["timestamp"], reverse=True):
        conn.execute(
            "INSERT INTO versions (timestamp, manifest_file, is_current) VALUES (?, ?, 0)",
            (row["timestamp"], row["manifest_file"]),
        )
        for name, bundle_hash in row["categories"].items():
            conn.execute(
                "INSERT INTO bundles (name, hash, size, ver, version_timestamp) VALUES (?, ?, 10, 7, ?)",
                (name, bundle_hash, row["timestamp"]),
            )
        for sub_hash in row["sub_hashes"]:
            conn.execute(
                "INSERT INTO sub_bundles (hash, category, version_timestamp) VALUES (?, 'Data', ?)",
                (sub_hash, row["timestamp"]),
            )
    conn.commit()
    conn.close()
    return path


def _png_bytes(width: int, height: int, seed: int = 0) -> bytes:
    def chunk(tag: bytes, payload: bytes) -> bytes:
        return (
            struct.pack(">I", len(payload))
            + tag
            + payload
            + struct.pack(">I", zlib.crc32(tag + payload) & 0xFFFFFFFF)
        )

    ihdr = struct.pack(">IIBBBBB", width, height, 8, 6, 0, 0, 0)
    row = b"\x00" + bytes([seed & 0xFF, 8, 16, 255]) * width
    return (
        b"\x89PNG\r\n\x1a\n"
        + chunk(b"IHDR", ihdr)
        + chunk(b"IDAT", zlib.compress(row * height))
        + chunk(b"IEND", b"")
    )


def _config(data_dir: Path, tmp: Path) -> ServerConfig:
    tools = tmp / "tools"
    (tools / "dotnet").mkdir(parents=True, exist_ok=True)
    (tools / "dotnet" / "dotnet").write_bytes(b"dotnet")
    (tools / "scene").mkdir(parents=True, exist_ok=True)
    (tools / "scene" / "PortraitSceneCLI.dll").write_bytes(b"cli")
    (tools / "as").mkdir(parents=True, exist_ok=True)
    (tools / "as" / "AssetStudioCLI").write_bytes(b"as")
    return ServerConfig(
        config_path=data_dir / "config.toml",
        data_dir=data_dir,
        cdn_base="https://example.invalid",
        timezone_name="Asia/Shanghai",
        normal_interval_seconds=3600,
        burst_interval_seconds=60,
        burst_duration_seconds=1200,
        burst_anchor=datetime(2026, 9, 18, 10, 0, tzinfo=UTC),
        selected_categories=("Arts", "Data"),
        java_bin="java",
        unluac_jar=data_dir / "unluac.jar",
        unluac_opmap=data_dir / "opmap",
        character_card_font=None,
        node_bin="node",
        assetbundle_key=b"yunguihaowan1234",
        render_cards=True,
        rerun_schedule_enabled=False,
        portrait_enabled=True,
        portrait_dotnet_path=tools / "dotnet" / "dotnet",
        portrait_scene_cli_path=tools / "scene" / "PortraitSceneCLI.dll",
        portrait_assetstudio_cli=tools / "as" / "AssetStudioCLI",
    )


class _FakeClient:
    """按 DB 中的 manifest_file 返回版本清单；只记录、不联网。"""

    def __init__(self, rows: list[dict]):
        self.rows = {row["manifest_file"]: row for row in rows}
        self.downloaded: list[str] = []

    def fetch_version_catalog(self, manifest_file: str) -> VersionCatalog:
        row = self.rows[str(manifest_file)]
        return VersionCatalog(
            timestamp=row["timestamp"],
            categories=tuple(
                CategoryInfo(name=name, hash=bundle_hash, size=1, version=7)
                for name, bundle_hash in row["categories"].items()
            ),
        )

    def download_category_catalog(self, category, category_hash, destination) -> Path:
        self.downloaded.append(f"catalog:{category}:{category_hash}")
        destination = Path(destination)
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_bytes(b"catalog")
        return destination

    def download_bundle(self, bundle_hash, destination) -> Path:
        self.downloaded.append(f"bundle:{bundle_hash}")
        destination = Path(destination)
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_bytes(f"bundle-{bundle_hash}".encode())
        return destination


def _install_render_fakes(monkeypatch, calls: dict, failures: set[str]) -> None:
    """把立绘渲染打到进程/下载边界以下：真实计划、继承、manifest、事件逻辑全部执行。

    ``failures`` 是可变集合，运行期读取：包含的皮肤渲染报错（用于失败/恢复场景）。
    """

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


def _catalog_by_filename(path, _key):
    name = Path(path).name
    if name.startswith("arts_"):
        # 回填按 DB 中的分类哈希下载清单，文件名携带哈希，借此区分版本
        return ARTS_CATALOG_V2 if "artshash200" in name else ARTS_CATALOG_V1
    return DATA_CATALOG


class _BackfillEnv:
    """虚构 DB + CDN + 渲染边界下的回填运行环境。"""

    def __init__(
        self,
        tmp_path: Path,
        monkeypatch,
        *,
        db_rows: list[dict] = DB_ROWS,
        failures: set[str] = frozenset(),
    ):
        self.tmp = tmp_path
        self.data_dir = tmp_path / "data"
        self.work_root = tmp_path / "backfill-work"
        self.db_path = _create_db(tmp_path / "xl_updata.db", db_rows)
        self.client = _FakeClient(db_rows)
        self.calls: dict = {"rendered": []}
        self.versions_plan = [
            {"characters": CHARACTERS_V1, "basefashion": BASE_FASHION_V1},
            {"characters": CHARACTERS_V2, "basefashion": BASE_FASHION_V2},
        ]
        self.config = _config(self.data_dir, tmp_path)
        # 可变失败集合：同环境可在多次运行间切换渲染失败（失败→修复→重跑）
        self.failures: set[str] = set(failures)
        _install_render_fakes(monkeypatch, self.calls, self.failures)
        monkeypatch.setattr(portrait_update, "read_catalog", _catalog_by_filename)
        try:
            module = _history_backfill()
        except ImportError:
            # 模块尚未实现时环境仍可用于“现有行为不变”对照用例
            module = None

        # 解码/解析 fake 按 staging 路径中的版本号取对应计划，多次运行稳定
        plan_by_version = {
            row["timestamp"]: plan for row, plan in zip(db_rows, self.versions_plan)
        }

        def fake_extract(bundle_paths, output_dir):
            output_dir = Path(output_dir)
            output_dir.mkdir(parents=True, exist_ok=True)
            written = []
            for index, _ in enumerate(bundle_paths):
                lua = output_dir / f"raw-{index}.lua"
                lua.write_bytes(b"raw lua")
                written.append(lua)
            return tuple(written)

        def fake_decode(_input_dir, output_dir, *args, **kwargs):
            plan = plan_by_version[int(Path(output_dir).parent.name)]
            output_dir = Path(output_dir)
            output_dir.mkdir(parents=True, exist_ok=True)
            (output_dir / "BaseFashion.lua").write_text(plan["basefashion"], encoding="utf-8")
            (output_dir / "basecard.lua").write_text("return {}", encoding="utf-8")
            return DecodeReport(success=2, failed=0)

        def fake_parse(decoded_dir):
            plan = plan_by_version[int(Path(decoded_dir).parent.name)]
            return {"index": [], "characters": plan["characters"], "words": {}}

        def fake_export(characters, output_dir, *args, **kwargs):
            only = kwargs.get("only_character_ids")
            wanted = [str(cid) for cid in (only if only is not None else characters)]
            output_dir = Path(output_dir)
            output_dir.mkdir(parents=True, exist_ok=True)
            outputs = []
            character_outputs = []
            for cid in sorted(wanted):
                path = output_dir / f"{cid}_测试_角色档案_长图.png"
                path.write_bytes(f"card-{cid}".encode())
                outputs.append(path)
                character_outputs.append((cid, path))
            return CardExportReport(
                seen=len(wanted),
                created=len(outputs),
                warned=0,
                failed=0,
                outputs=tuple(outputs),
                warnings=(),
                failures=(),
                character_outputs=tuple(character_outputs),
            )

        if module is not None:
            monkeypatch.setattr(module, "read_catalog", _catalog_by_filename)
            monkeypatch.setattr(module, "extract_lua_files", fake_extract)
            monkeypatch.setattr(module, "decode_lua_directory", fake_decode)
            monkeypatch.setattr(module, "parse_character_snapshot", fake_parse)
            monkeypatch.setattr(module, "export_character_cards", fake_export)

    def run(self, **kwargs):
        return _history_backfill().run_backfill(
            config=self.config,
            client=self.client,
            db_path=self.db_path,
            work_root=self.work_root,
            **kwargs,
        )


def _events(data_dir: Path) -> list[dict]:
    log = data_dir / "update_events.jsonl"
    if not log.is_file():
        return []
    return [json.loads(line) for line in log.read_text(encoding="utf-8").splitlines() if line.strip()]


def _version_manifest(work_root: Path, version: int, name: str) -> dict:
    path = work_root / "versions" / str(version) / name
    return json.loads(path.read_text(encoding="utf-8"))


def _skins_by_id(manifest: dict) -> dict[str, dict]:
    return {entry["skin_id"]: entry for entry in manifest["skins"]}


# ---- 只读历史索引 ------------------------------------------------------------


def test_read_history_index_orders_old_to_new_with_catalog_data(tmp_path):
    db_path = _create_db(tmp_path / "xl_updata.db", DB_ROWS)

    index = _history_backfill().read_history_index(db_path)

    assert [item.timestamp for item in index] == [100, 200]
    first, second = index
    assert first.manifest_file == "100/Android/versions.json"
    assert second.manifest_file == "200/Android/versions.json"
    first_categories = {entry.name: entry.hash for entry in first.categories}
    assert first_categories == {"Data": "datahash100", "Arts": "artshash100"}
    assert first.bundle_hashes == ("suba100", "subb100")
    assert second.bundle_hashes == ("suba200",)


def test_read_history_index_opens_db_readonly_without_side_effects(tmp_path):
    db_path = _create_db(tmp_path / "xl_updata.db", DB_ROWS)
    before = db_path.read_bytes()
    os.chmod(db_path, 0o444)
    try:
        index = _history_backfill().read_history_index(db_path)
    finally:
        os.chmod(db_path, 0o666)

    assert [item.timestamp for item in index] == [100, 200]
    assert db_path.read_bytes() == before
    for suffix in ("-journal", "-wal", "-shm"):
        assert not (tmp_path / f"xl_updata.db{suffix}").exists()


def test_read_history_index_missing_db_raises(tmp_path):
    with pytest.raises(FileNotFoundError):
        _history_backfill().read_history_index(tmp_path / "missing.db")


# ---- 预检与执行 --------------------------------------------------------------

def test_run_backfill_preflight_by_default_writes_nothing(tmp_path, monkeypatch):
    env = _BackfillEnv(tmp_path, monkeypatch)

    report = env.run()

    assert [item.version for item in report.versions] == [100, 200]
    assert all(item.status == "pending" for item in report.versions)
    assert env.client.downloaded == []
    assert env.calls["rendered"] == []
    # 工作根与生产数据根都不产生任何产物
    assert not (env.work_root / "versions").exists()
    assert not (env.data_dir / "versions").exists()
    assert _events(env.data_dir) == []


def test_run_backfill_execute_renders_oldest_baseline_and_suppresses_notifications(tmp_path, monkeypatch):
    env = _BackfillEnv(tmp_path, monkeypatch)

    report = env.run(execute=True)

    assert [(item.version, item.status) for item in report.versions] == [(100, "completed"), (200, "completed")]
    # 最旧版本是真实渲染的完整基线，而不是正常更新的“只记录指纹”
    assert env.calls["rendered"][:3] == ["12001513", "12001514", "12001515"]
    # 后续版本只渲染新增角色皮肤，旧皮肤继承不重渲
    assert env.calls["rendered"][3:] == ["14001520", "14001521"]

    # 立绘基线 manifest：全部 published 且 source_version = 基线版本
    baseline = _version_manifest(env.work_root, 100, "portraits/manifest.json")
    baseline_skins = _skins_by_id(baseline)
    assert set(baseline_skins) == {"12001513", "12001514", "12001515"}
    for entry in baseline_skins.values():
        assert entry["state"] == "published"
        assert entry["source_version"] == 100
        assert (env.work_root / "versions" / "100" / "portraits" / entry["artifact_path"]).is_file()
        digest = hashlib.sha256(
            (env.work_root / "versions" / "100" / "portraits" / entry["artifact_path"]).read_bytes()
        ).hexdigest()
        assert entry["artifact_sha256"] == digest

    # 第二版：旧皮肤继承并保留原 source_version，新皮肤 published
    second = _version_manifest(env.work_root, 200, "portraits/manifest.json")
    second_skins = _skins_by_id(second)
    assert second_skins["12001513"]["state"] == "published"
    assert second_skins["12001513"]["source_version"] == 100
    assert second_skins["14001520"]["state"] == "published"
    assert second_skins["14001520"]["source_version"] == 200
    version_root = env.work_root / "versions" / "200"
    versioned_data = version_root / "character_data" / "versions" / "200.json"
    assert versioned_data.is_file()
    assert versioned_data.read_bytes() == (version_root / "character_data" / "current.json").read_bytes()

    # 每版图鉴 card manifest 记录有效条目与内容哈希
    cards_100 = _version_manifest(env.work_root, 100, "character_cards/manifest.json")
    card_entries = {entry["character_id"]: entry for entry in cards_100["characters"]}
    assert set(card_entries) == {"10000151", "10000224"}
    for entry in card_entries.values():
        assert entry["state"] == "published"
        assert entry["source_version"] == "100"
        assert entry["data_sha256"]
        artifact = env.work_root / "versions" / "100" / entry["artifact_path"]
        assert artifact.is_file()
        assert entry["artifact_sha256"] == hashlib.sha256(artifact.read_bytes()).hexdigest()
    cards_200 = _version_manifest(env.work_root, 200, "character_cards/manifest.json")
    entries_200 = {entry["character_id"]: entry for entry in cards_200["characters"]}
    assert entries_200["10000333"]["state"] == "published"
    assert entries_200["10000151"]["source_version"] == "100"

    # 通知与生产状态零改动：无事件、无 outbox、无 portrait_outbox、无 current 指针
    assert _events(env.data_dir) == []
    assert not (env.data_dir / "update_events.jsonl").exists()
    assert not (env.data_dir / "outbox").exists()
    assert not (env.data_dir / "portrait_outbox").exists()
    assert not (env.data_dir / "current_version.json").exists()
    assert not (env.data_dir / "versions").exists()


def test_run_backfill_keeps_normal_update_first_run_fingerprint_only(tmp_path, monkeypatch):
    """对照：同一输入走正常 run_portrait_update 仍然只记录指纹（默认行为不变）。"""
    env = _BackfillEnv(tmp_path, monkeypatch)
    decoded = tmp_path / "decoded-normal"
    decoded.mkdir()
    (decoded / "BaseFashion.lua").write_text(BASE_FASHION_V1, encoding="utf-8")
    staging = tmp_path / "staging-normal"
    staging.mkdir()

    summary = portrait_update.run_portrait_update(
        config=env.config,
        client=env.client,
        catalog_dir=env.data_dir / "catalogs",
        version=100,
        decoded_lua=decoded,
        staging=staging,
        characters=CHARACTERS_V1,
        arts_category=CategoryInfo(name="Arts", hash="artshash100", size=1, version=7),
    )

    assert env.calls["rendered"] == []
    assert summary.success == 0 and summary.skipped == 3
    manifest = json.loads((staging / "portraits" / "manifest.json").read_text(encoding="utf-8"))
    assert all(entry["state"] == "not_generated" for entry in manifest["skins"])
    assert _events(env.data_dir) == []


def test_run_backfill_is_idempotent_and_resumes_without_rerendering(tmp_path, monkeypatch):
    env = _BackfillEnv(tmp_path, monkeypatch)
    env.run(execute=True)
    rendered_after_first = list(env.calls["rendered"])
    manifests_after_first = {
        name: (env.work_root / "versions" / name).read_bytes()
        for name in ("100/portraits/manifest.json", "200/portraits/manifest.json",
                     "100/character_cards/manifest.json", "200/character_cards/manifest.json")
    }

    second = env.run(execute=True)

    assert [(item.version, item.status) for item in second.versions] == [(100, "completed"), (200, "completed")]
    assert env.calls["rendered"] == rendered_after_first
    for name, content in manifests_after_first.items():
        assert (env.work_root / "versions" / name).read_bytes() == content
    assert _events(env.data_dir) == []


def test_run_backfill_versions_filter_limits_processed_versions(tmp_path, monkeypatch):
    env = _BackfillEnv(tmp_path, monkeypatch)

    report = env.run(execute=True, versions=[200])

    assert [item.version for item in report.versions] == [200]
    assert not (env.work_root / "versions" / "100").exists()
    assert (env.work_root / "versions" / "200").is_dir()


# ---- 发布 --------------------------------------------------------------------

def _canonical_character(data) -> str:
    return json.dumps(data, sort_keys=True, ensure_ascii=False, default=str)


def _build_validated_version(work_root: Path, version: int, characters: dict, skin_ids: tuple[str, ...]) -> Path:
    """构造一份自洽（路径/SHA 全部吻合）的已回填版本目录。"""
    root = work_root / "versions" / str(version)
    cards_dir = root / "character_cards"
    cards_dir.mkdir(parents=True, exist_ok=True)
    entries = []
    for cid, data in sorted(characters.items()):
        png = _png_bytes(20, 20, seed=(version + sum(ord(c) for c in cid)) & 0xFF)
        filename = f"{cid}_测试_角色档案_长图.png"
        (cards_dir / filename).write_bytes(png)
        entries.append({
            "character_id": cid,
            "state": "published",
            "artifact_path": f"character_cards/{filename}",
            "artifact_sha256": hashlib.sha256(png).hexdigest(),
            "data_sha256": hashlib.sha256(_canonical_character(data).encode("utf-8")).hexdigest(),
            "source_version": str(version),
            "error": None,
        })
    (cards_dir / "manifest.json").write_text(
        json.dumps({"schema_version": 1, "version": str(version), "characters": entries}, ensure_ascii=False),
        encoding="utf-8",
    )
    (root / "character_data").mkdir(parents=True, exist_ok=True)
    (root / "character_data" / "current.json").write_text(
        json.dumps({"version": version, "characters": characters}, ensure_ascii=False), encoding="utf-8"
    )
    portraits = []
    (root / "portraits").mkdir(parents=True, exist_ok=True)
    for skin_id in skin_ids:
        png = _png_bytes(30, 22, seed=int(skin_id) & 0xFF)
        relative = Path("10000151") / f"{skin_id}.png"
        (root / "portraits" / relative).parent.mkdir(parents=True, exist_ok=True)
        (root / "portraits" / relative).write_bytes(png)
        portraits.append({
            "skin_id": skin_id,
            "character_id": "10000151",
            "ordinal": 1,
            "state": "published",
            "artifact_path": relative.as_posix(),
            "artifact_sha256": hashlib.sha256(png).hexdigest(),
            "source_version": version,
        })
    (root / "portraits" / "manifest.json").write_text(
        json.dumps({"schema_version": 1, "version": str(version), "skins": portraits}, ensure_ascii=False),
        encoding="utf-8",
    )
    # 合法的全量完成标记：发布保护要求 status=completed + full_scope + 零失败立绘
    (root / "backfill.json").write_text(
        json.dumps(
            {"version": version, "status": "completed", "full_scope": True, "failed_skins": 0},
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    return root


def _production_root(tmp_path: Path) -> Path:
    production = tmp_path / "production"
    (production / "character_data").mkdir(parents=True)
    (production / "character_data" / "current.json").write_text(
        '{"version": 900, "characters": {"1": {}}}', encoding="utf-8"
    )
    (production / "current_version.json").write_text('{"version": 900}', encoding="utf-8")
    (production / "update_events.jsonl").write_text('{"event": "finish", "version": 900}\n', encoding="utf-8")
    (production / "outbox" / "900").mkdir(parents=True)
    (production / "outbox" / "900" / "manifest.json").write_text('{"version": 900}', encoding="utf-8")
    (production / "portrait_outbox" / "900").mkdir(parents=True)
    (production / "portrait_outbox" / "900" / "x.png").write_bytes(b"sent")
    return production


def test_publish_backfill_adds_only_missing_versions_without_touching_production_state(tmp_path):
    work_root = tmp_path / "backfill-work"
    _build_validated_version(work_root, 100, CHARACTERS_V1, ("12001513",))
    # 工作根 200 的清单引用工作根自己的文件（文件名与生产完全不同），
    # 防止实现把工作根 manifest 直接拷进生产后引用不存在的图片。
    _build_validated_version(work_root, 200, CHARACTERS_V2, ("14001520",))
    production = _production_root(tmp_path)
    existing = production / "versions" / "200"
    legacy_cards = {
        "10000151_篝火_角色档案_长图.png": b"legacy-png-10000151",
        "10000224_雾铃_角色档案_长图.png": b"legacy-png-10000224",
    }
    for name, body in legacy_cards.items():
        (existing / "character_cards" / name).parent.mkdir(parents=True, exist_ok=True)
        (existing / "character_cards" / name).write_bytes(body)
    (existing / "do-not-touch.txt").write_text("original", encoding="utf-8")
    (existing / "character_data").mkdir()
    (existing / "character_data" / "current.json").write_text(
        json.dumps(
            {"version": 200, "characters": {"10000151": {"name": "篝火"}, "10000224": {"name": "雾铃"}}},
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )

    sentinel_before = {
        "current_version.json": (production / "current_version.json").read_bytes(),
        "character_data/current.json": (production / "character_data" / "current.json").read_bytes(),
        "update_events.jsonl": (production / "update_events.jsonl").read_bytes(),
        "outbox/900/manifest.json": (production / "outbox" / "900" / "manifest.json").read_bytes(),
        "portrait_outbox/900/x.png": (production / "portrait_outbox" / "900" / "x.png").read_bytes(),
    }

    report = _history_backfill().publish_backfill(
        work_root=work_root, production_data_dir=production, versions=[100, 200]
    )

    assert 100 in report.published
    assert 200 not in report.published
    assert 200 in report.merged  # 只补缺失的派生 card manifest
    # 缺失版本整体合入
    published_root = production / "versions" / "100"
    assert (published_root / "character_cards" / "manifest.json").is_file()
    assert (published_root / "portraits" / "manifest.json").is_file()
    assert (published_root / "character_data" / "current.json").is_file()
    # 已存在版本：图片与角色 JSON 不被覆盖
    for name, body in legacy_cards.items():
        assert (existing / "character_cards" / name).read_bytes() == body
    assert (existing / "do-not-touch.txt").read_text(encoding="utf-8") == "original"
    assert set(json.loads((existing / "character_data" / "current.json").read_text(encoding="utf-8"))["characters"]) == {"10000151", "10000224"}
    # 补进的 card manifest 必须按生产实际文件/角色数据重建并重新验证：
    # 只引用生产存在的图片、哈希与生产字节一致、覆盖生产角色、不得出现工作根专属文件名。
    merged_manifest = json.loads((existing / "character_cards" / "manifest.json").read_text(encoding="utf-8"))
    entries = {entry["character_id"]: entry for entry in merged_manifest["characters"]}
    assert set(entries) == {"10000151", "10000224"}
    for entry in entries.values():
        artifact = existing / entry["artifact_path"]
        assert artifact.is_file()
        assert entry["artifact_sha256"] == hashlib.sha256(artifact.read_bytes()).hexdigest()
        assert "_测试_" not in entry["artifact_path"]
    assert "10000333" not in entries
    # 生产当前指针/事件/outbox 逐项字节不变
    for name, content in sentinel_before.items():
        assert (production / name).read_bytes() == content
    assert not (production / "current_version.json.part").exists()
    for path in production.rglob("*.part"):
        raise AssertionError(f"发布残留临时文件: {path}")


def test_publish_backfill_leaves_existing_production_manifest_untouched(tmp_path):
    work_root = tmp_path / "backfill-work"
    _build_validated_version(work_root, 100, CHARACTERS_V1, ("12001513",))
    production = _production_root(tmp_path)
    existing = production / "versions" / "100"
    (existing / "character_cards").mkdir(parents=True)
    original = json.dumps(
        {"schema_version": 1, "version": "100", "characters": []}, ensure_ascii=False
    )
    (existing / "character_cards" / "manifest.json").write_text(original, encoding="utf-8")

    report = _history_backfill().publish_backfill(
        work_root=work_root, production_data_dir=production, versions=[100]
    )

    assert report.published == ()
    assert report.merged == ()
    assert report.rejected == ()
    assert (existing / "character_cards" / "manifest.json").read_text(encoding="utf-8") == original


def test_publish_backfill_rejects_versions_failing_validation(tmp_path):
    work_root = tmp_path / "backfill-work"
    _build_validated_version(work_root, 100, CHARACTERS_V1, ("12001513",))
    broken = _build_validated_version(work_root, 300, {"10000224": {"name": "雾铃"}}, ())
    # 破坏校验：manifest 引用的图片不存在
    manifest_path = broken / "character_cards" / "manifest.json"
    payload = json.loads(manifest_path.read_text(encoding="utf-8"))
    payload["characters"][0]["artifact_path"] = "character_cards/已被删除.png"
    manifest_path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
    production = _production_root(tmp_path)

    report = _history_backfill().publish_backfill(
        work_root=work_root, production_data_dir=production, versions=[100, 300]
    )

    assert 100 in report.published
    assert 300 in report.rejected
    assert not (production / "versions" / "300").exists()
    assert (production / "current_version.json").read_bytes() == b'{"version": 900}'


# ---- 复审缺口回归 -------------------------------------------------------------


def test_bundle_cache_client_reuses_valid_hash_cache_and_redownloads_corruption(tmp_path):
    from server_app.history_backfill import BundleCacheClient

    content = b"verified bundle bytes"
    bundle_hash = hashlib.md5(content).hexdigest()
    calls: list[str] = []

    class _StubClient:
        def download_bundle(self, hash_value, destination):
            calls.append(str(hash_value))
            destination = Path(destination)
            destination.parent.mkdir(parents=True, exist_ok=True)
            destination.write_bytes(content)
            return destination

    cache_client = BundleCacheClient(_StubClient(), tmp_path / "cache")
    first = cache_client.download_bundle(bundle_hash, tmp_path / "a" / f"{bundle_hash}.bundle")
    assert calls == [bundle_hash]
    assert first.read_bytes() == content

    second = cache_client.download_bundle(bundle_hash, tmp_path / "b" / f"{bundle_hash}.bundle")
    assert calls == [bundle_hash]  # 缓存命中，不再下载
    assert second.read_bytes() == content

    (tmp_path / "cache" / f"{bundle_hash}.bundle").write_bytes(b"corrupted")
    third = cache_client.download_bundle(bundle_hash, tmp_path / "c" / f"{bundle_hash}.bundle")
    assert calls == [bundle_hash, bundle_hash]  # MD5 不符 → 删缓存重下
    assert third.read_bytes() == content


def _progress(work_root: Path, version: int) -> dict:
    return json.loads((work_root / "versions" / str(version) / "backfill.json").read_text(encoding="utf-8"))


def test_run_backfill_binds_completion_scope_to_sample_filter(tmp_path, monkeypatch):
    env = _BackfillEnv(tmp_path, monkeypatch)

    sample = env.run(execute=True, versions=[100], only_character_ids=["10000151"])
    assert [(item.version, item.status) for item in sample.versions] == [(100, "completed")]
    progress = _progress(env.work_root, 100)
    assert progress["full_scope"] is False
    assert progress["scope"] == {"characters": ["10000151"], "skins": None}
    cards_after_sample = {
        entry["character_id"]
        for entry in _version_manifest(env.work_root, 100, "character_cards/manifest.json")["characters"]
    }
    assert cards_after_sample == {"10000151"}

    # 同范围重复执行幂等跳过：产物不变
    again = env.run(execute=True, versions=[100], only_character_ids=["10000151"])
    assert [(item.version, item.status) for item in again.versions] == [(100, "completed")]
    assert {
        entry["character_id"]
        for entry in _version_manifest(env.work_root, 100, "character_cards/manifest.json")["characters"]
    } == cards_after_sample

    # 后续全量运行不得直接跳过样本版本：重新处理并写回全量完成记录
    full = env.run(execute=True, versions=[100])
    assert [(item.version, item.status) for item in full.versions] == [(100, "completed")]
    cards_after_full = {
        entry["character_id"]
        for entry in _version_manifest(env.work_root, 100, "character_cards/manifest.json")["characters"]
    }
    assert cards_after_full == {"10000151", "10000224"}
    progress = _progress(env.work_root, 100)
    assert progress["full_scope"] is True
    assert progress["scope"] == {"characters": None, "skins": None}


def test_run_backfill_skin_sample_filter_limits_renders_and_blocks_publish(tmp_path, monkeypatch):
    env = _BackfillEnv(tmp_path, monkeypatch)

    report = env.run(execute=True, versions=[100], only_skin_ids=["12001514"])

    assert [(item.version, item.status) for item in report.versions] == [(100, "completed")]
    assert env.calls["rendered"] == ["12001514"]  # 显式皮肤过滤，不渲染该角色其余皮肤
    progress = _progress(env.work_root, 100)
    assert progress["scope"] == {"characters": None, "skins": ["12001514"]}
    assert progress["full_scope"] is False
    skins = {entry["skin_id"]: entry for entry in _version_manifest(env.work_root, 100, "portraits/manifest.json")["skins"]}
    assert skins["12001514"]["state"] == "published"
    assert skins["12001513"]["state"] == "not_applicable"

    production = _production_root(tmp_path)
    publish = _history_backfill().publish_backfill(
        work_root=env.work_root, production_data_dir=production, versions=[100]
    )
    assert 100 in publish.rejected  # 过滤样本不得发布
    assert not (production / "versions" / "100").exists()


def test_run_backfill_marks_failed_skins_not_completed_and_unpublishable(tmp_path, monkeypatch):
    env = _BackfillEnv(tmp_path, monkeypatch, failures={"12001514"})

    report = env.run(execute=True, versions=[100])

    assert [(item.version, item.status) for item in report.versions] == [(100, "failed")]
    assert "1 套立绘渲染失败" in report.versions[0].error
    progress = _progress(env.work_root, 100)
    assert progress["status"] == "failed"
    assert progress["failed_skins"] == 1
    assert progress["full_scope"] is False

    production = _production_root(tmp_path)
    publish = _history_backfill().publish_backfill(
        work_root=env.work_root, production_data_dir=production, versions=[100]
    )
    assert 100 in publish.rejected
    assert not (production / "versions" / "100").exists()


def test_publish_backfill_requires_full_scope_zero_failed_completion(tmp_path):
    work_root = tmp_path / "backfill-work"
    # 100：样本完成标记（full_scope=False）
    _build_validated_version(work_root, 100, CHARACTERS_V1, ("12001513",))
    _write_progress(work_root, 100, {"status": "completed", "full_scope": False, "failed_skins": 0})
    # 200：完全没有完成标记
    _build_validated_version(work_root, 200, CHARACTERS_V1, ("12001513",))
    (work_root / "versions" / "200" / "backfill.json").unlink()
    # 300：完成但带失败立绘
    _build_validated_version(work_root, 300, CHARACTERS_V1, ("12001513",))
    _write_progress(work_root, 300, {"status": "completed", "full_scope": True, "failed_skins": 2})
    production = _production_root(tmp_path)

    report = _history_backfill().publish_backfill(
        work_root=work_root, production_data_dir=production, versions=[100, 200, 300]
    )

    assert report.published == ()
    assert set(report.rejected) == {100, 200, 300}
    for version in (100, 200, 300):
        assert not (production / "versions" / str(version)).exists()


def _write_progress(work_root: Path, version: int, payload: dict) -> None:
    (work_root / "versions" / str(version) / "backfill.json").write_text(
        json.dumps({"version": version, **payload}, ensure_ascii=False), encoding="utf-8"
    )


def test_publish_backfill_rejects_missing_or_malformed_portraits_manifest(tmp_path):
    work_root = tmp_path / "backfill-work"
    missing = _build_validated_version(work_root, 100, CHARACTERS_V1, ("12001513",))
    (missing / "portraits" / "manifest.json").unlink()
    not_object = _build_validated_version(work_root, 200, CHARACTERS_V1, ("12001513",))
    (not_object / "portraits" / "manifest.json").write_text("[]", encoding="utf-8")
    bad_skins = _build_validated_version(work_root, 300, CHARACTERS_V1, ("12001513",))
    (bad_skins / "portraits" / "manifest.json").write_text('{"skins": {}}', encoding="utf-8")
    production = _production_root(tmp_path)

    report = _history_backfill().publish_backfill(
        work_root=work_root, production_data_dir=production, versions=[100, 200, 300]
    )

    assert report.published == ()
    assert set(report.rejected) == {100, 200, 300}


def test_publish_merge_rebuild_skips_ambiguous_rename_candidates(tmp_path):
    work_root = tmp_path / "backfill-work"
    _build_validated_version(work_root, 200, CHARACTERS_V2, ("14001520",))
    production = _production_root(tmp_path)
    existing = production / "versions" / "200"
    # 生产 200：10000151 有改名残留两张图且根 manifest 无 outputs 声明 → 不得猜测
    legacy_cards = {
        "10000151_旧名_角色档案_长图.png": b"legacy-old",
        "10000151_新名_角色档案_长图.png": b"legacy-new",
        "10000224_雾铃_角色档案_长图.png": b"legacy-wuling",
    }
    for name, body in legacy_cards.items():
        (existing / "character_cards" / name).parent.mkdir(parents=True, exist_ok=True)
        (existing / "character_cards" / name).write_bytes(body)
    (existing / "character_data").mkdir()
    (existing / "character_data" / "current.json").write_text(
        json.dumps({"version": 200, "characters": {"10000151": {"name": "篝火"}, "10000224": {"name": "雾铃"}}}, ensure_ascii=False),
        encoding="utf-8",
    )
    (existing / "manifest.json").write_text('{"version": 200}', encoding="utf-8")

    report = _history_backfill().publish_backfill(
        work_root=work_root, production_data_dir=production, versions=[200]
    )

    assert 200 in report.merged
    merged_manifest = json.loads((existing / "character_cards" / "manifest.json").read_text(encoding="utf-8"))
    entries = {entry["character_id"]: entry for entry in merged_manifest["characters"]}
    assert set(entries) == {"10000224"}  # 歧义角色被跳过，绝不猜
    for name, body in legacy_cards.items():
        assert (existing / "character_cards" / name).read_bytes() == body


def test_publish_merge_rebuild_prefers_declared_output_on_rename(tmp_path):
    work_root = tmp_path / "backfill-work"
    _build_validated_version(work_root, 200, CHARACTERS_V2, ("14001520",))
    production = _production_root(tmp_path)
    existing = production / "versions" / "200"
    legacy_cards = {
        "10000151_旧名_角色档案_长图.png": b"legacy-old",
        "10000151_新名_角色档案_长图.png": b"legacy-new",
    }
    for name, body in legacy_cards.items():
        (existing / "character_cards" / name).parent.mkdir(parents=True, exist_ok=True)
        (existing / "character_cards" / name).write_bytes(body)
    (existing / "character_data").mkdir()
    (existing / "character_data" / "current.json").write_text(
        json.dumps({"version": 200, "characters": {"10000151": {"name": "篝火"}}}, ensure_ascii=False),
        encoding="utf-8",
    )
    (existing / "manifest.json").write_text(json.dumps({
        "version": 200,
        "character_cards": {"outputs": ["character_cards/10000151_新名_角色档案_长图.png"]},
    }), encoding="utf-8")

    report = _history_backfill().publish_backfill(
        work_root=work_root, production_data_dir=production, versions=[200]
    )

    assert 200 in report.merged
    merged_manifest = json.loads((existing / "character_cards" / "manifest.json").read_text(encoding="utf-8"))
    (entry,) = merged_manifest["characters"]
    assert entry["artifact_path"] == "character_cards/10000151_新名_角色档案_长图.png"
    assert entry["artifact_sha256"] == hashlib.sha256(b"legacy-new").hexdigest()


# ---- 独立审查缺口回归 ----------------------------------------------------------


def test_rerun_failed_old_version_never_inherits_from_future_versions(tmp_path, monkeypatch):
    env = _BackfillEnv(tmp_path, monkeypatch, failures={"12001514"})
    first = env.run(execute=True, versions=[100])
    assert [(item.version, item.status) for item in first.versions] == [(100, "failed")]

    env.failures.clear()
    second = env.run(execute=True, versions=[200])
    assert [(item.version, item.status) for item in second.versions] == [(200, "completed")]

    # 修复后重跑失败的 100：不得从未来版本 200 继承产物/source_version。
    # 重跑只重试失败项（12001514），已成功项走同版本恢复（done/emit）不重渲。
    renders_before_third = list(env.calls["rendered"])
    third = env.run(execute=True, versions=[100])
    assert [(item.version, item.status) for item in third.versions] == [(100, "completed")]
    rerun_renders = env.calls["rendered"][len(renders_before_third):]
    assert rerun_renders == ["12001514"]
    manifest = _version_manifest(env.work_root, 100, "portraits/manifest.json")
    for entry in manifest["skins"]:
        assert entry["state"] == "published"
        assert entry["source_version"] == 100  # 未来版本 200 的产物/source 不得混入
        artifact = env.work_root / "versions" / "100" / "portraits" / entry["artifact_path"]
        assert artifact.is_file()
        assert entry["artifact_sha256"] == hashlib.sha256(artifact.read_bytes()).hexdigest()


def test_next_version_ignores_failed_version_card_manifest_as_base(tmp_path, monkeypatch):
    env = _BackfillEnv(tmp_path, monkeypatch, failures={"12001514"})
    failed = env.run(execute=True, versions=[100])
    assert [(item.version, item.status) for item in failed.versions] == [(100, "failed")]
    assert _progress(env.work_root, 100)["status"] == "failed"

    env.failures.clear()
    # 只处理 200：失败版 100 的 card manifest 不得作为基座，200 必须自建完整基线
    renders_before_second = list(env.calls["rendered"])
    second = env.run(execute=True, versions=[200])
    assert [(item.version, item.status) for item in second.versions] == [(200, "completed")]
    renders_for_200 = env.calls["rendered"][len(renders_before_second):]
    assert renders_for_200 == ["12001513", "12001514", "12001515", "14001520", "14001521"]
    cards = _version_manifest(env.work_root, 200, "character_cards/manifest.json")
    for entry in cards["characters"]:
        assert entry["state"] == "published"
        assert entry["source_version"] == "200"


def test_character_sample_filter_excludes_other_characters_skins(tmp_path, monkeypatch):
    env = _BackfillEnv(tmp_path, monkeypatch)

    report = env.run(execute=True, versions=[100], only_character_ids=["10000224"])

    assert [(item.version, item.status) for item in report.versions] == [(100, "completed")]
    # 只渲染样本角色 10000224 的皮肤；10000151 的两套不得渲染
    assert env.calls["rendered"] == ["12001515"]
    skins = {entry["skin_id"]: entry for entry in _version_manifest(env.work_root, 100, "portraits/manifest.json")["skins"]}
    assert skins["12001515"]["state"] == "published"
    assert skins["12001513"]["state"] == "not_applicable"
    assert skins["12001514"]["state"] == "not_applicable"


def test_character_and_skin_sample_filters_combine(tmp_path, monkeypatch):
    env = _BackfillEnv(tmp_path, monkeypatch)

    env.run(execute=True, versions=[100], only_character_ids=["10000224"], only_skin_ids=["12001513", "12001515"])

    # 两个集合同时生效：只渲染交集中的皮肤（12001513 属于 10000151，被角色集合排除）
    assert env.calls["rendered"] == ["12001515"]
    progress = _progress(env.work_root, 100)
    assert progress["scope"] == {"characters": ["10000224"], "skins": ["12001513", "12001515"]}
    assert progress["full_scope"] is False


def test_load_previous_manifest_never_selects_future_versions(tmp_path, monkeypatch):
    data_dir = tmp_path / "data"
    config = _config(data_dir, tmp_path)
    runner = portrait_update.PortraitUpdateRunner(
        config, _FakeClient(DB_ROWS), data_dir / "catalogs"
    )
    versions = data_dir / "versions"
    for version in (100, 200):
        portraits = versions / str(version) / "portraits"
        portraits.mkdir(parents=True)
        (portraits / "manifest.json").write_text(
            json.dumps({"schema_version": 1, "version": str(version), "skins": []}), encoding="utf-8"
        )

    # 失败版本 100 重跑时：未来版本 200 已存在也不得被选为上一版
    previous, previous_version = runner._load_previous_manifest("100")
    assert previous is None
    assert previous_version is None

    previous, previous_version = runner._load_previous_manifest("200")
    assert previous_version == "100"
    assert previous["version"] == "100"
