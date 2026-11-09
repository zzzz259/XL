"""增量立绘后处理：计划 → 逐套渲染 → 校验 → manifest → outbox → 事件。

接在现有 Lua 解码和图鉴处理之后运行；功能默认关闭，由实例配置启用。
严格使用版本产物契约 ``data/versions/<v>/portraits/{manifest.json,BaseFashion.lua,<char>/<skin>.png}``
和持久 outbox ``data/portrait_outbox/<v>/``；首次无基线只记录指纹
``not_generated``，不自动补历史；失败项留在 manifest，同版本恢复只重做未完成项。

同版本目录语义：``data/versions/<v>`` 对角色核心数据（character_data、
character_cards、manifest.json）不可变。processor 在
``current_version == update_info.timestamp`` 时仅当存在未完成立绘才进入恢复，
恢复直接以当前版本目录为输出、只原子替换 ``portraits/`` 下的立绘产物，
不重跑角色卡片/图鉴 outbox、不重复 start/finish 事件、不重复 publish_version。

中断安全：work manifest 逐套持久化（可能被中断截断为前缀，无害）；staging /
已发布 manifest 只在完整一轮全部结束后原子写一次，恢复中任何中断都保留
原有完整已发布 manifest。恢复门控同时读 work manifest 与已发布 manifest，
work 前缀缺失的已完成项由已发布 manifest 播种，不重渲、不重复发事件。

新渲染写入顺序固定为：PNG 完成并校验 → 原子写版本暂存产物与持久 outbox →
持久化 work manifest(event_emitted=False) → 追加 portrait_ready → 标
event_emitted=True 并再次持久化 work manifest。事件前崩溃恢复直接 emit 补发、
不重渲；事件后崩溃产生的重复事件由 Bot 幂等键去重。
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
import shutil
import signal
import struct
import subprocess
import sys
import time
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

from .catalog import read_catalog
from .cdn import CdnClient
from .character_adapter import _tool_app_root
from .config import ServerConfig
from .extractor import repair_bundle_header
from .selector import bundle_hashes_for_assets, select_portrait_prefab_assets

LOGGER = logging.getLogger(__name__)

MANIFEST_SCHEMA_VERSION = 1
PNG_SIGNATURE = b"\x89PNG\r\n\x1a\n"
# QQ 图片硬限（20MiB）：发布/事件/查询任何送达 Gateway 的立绘前必须守住。
MAX_PORTRAIT_IMAGE_BYTES = 20 * 1024 * 1024
# 与 Tool Importer 的 AssetStudio map 命令保持一致，不另造 AB 解析。
_AS_MAP_ARGS = ("--game", "UnityCN", "--key_index", "23", "--map_op", "Both", "--map_type", "JSON")
# 工作 manifest 里的控制字段，写入版本产物前剥掉。
_CONTROL_FIELDS = ("event_emitted", "staged")


class PortraitUpdateError(RuntimeError):
    """共享输入不可用，立绘步骤整体无法计划；不影响主数据流程。"""


def _ensure_png_within_limit(path: Path, *, limit: int | None = None) -> None:
    """stat 先行：超过 QQ 图片上限即抛可行动错误，不把大文件读进内存。"""
    if limit is None:
        limit = MAX_PORTRAIT_IMAGE_BYTES
    size = Path(path).stat().st_size
    if size > limit:
        raise ValueError(
            f"立绘 PNG {size} 字节超过 QQ 图片 {limit} 字节上限"
            f"（需降低单图体积后重渲）: {path}"
        )


def _job_start_new_session() -> bool:
    """POSIX 上把渲染子进程放进独立进程组，超时后整组收割。"""
    return os.name == "posix"


# signal.SIGKILL 在 POSIX 上恒为 9；Windows 的 signal 模块没有该属性。
_SIGKILL = getattr(signal, "SIGKILL", 9)


def _terminate_job_process(process) -> None:
    """超时后收割外层渲染子进程：POSIX 杀整个进程组，其余平台只杀直接子进程。"""
    if _job_start_new_session():
        try:
            os.killpg(os.getpgid(process.pid), _SIGKILL)
        except OSError:
            process.kill()
    else:
        process.kill()
    try:
        process.wait(timeout=15)  # 收割，避免僵尸
    except subprocess.TimeoutExpired:
        pass


@dataclass(frozen=True)
class PortraitSummary:
    success: int = 0
    failed: int = 0
    skipped: int = 0


@dataclass
class _SkinTask:
    skin_id: str
    character_id: str
    ordinal: int
    name: str
    prefab_name: str
    bundle_hashes: tuple[str, ...]
    fingerprint: str
    action: str  # render | inherit | skip
    previous: dict | None = None
    work_entry: dict | None = field(default=None, repr=False)


def run_portrait_update(
    *,
    config: ServerConfig,
    client: CdnClient,
    catalog_dir: Path,
    version: int,
    decoded_lua: Path,
    staging: Path,
    characters: dict,
    arts_category,
) -> PortraitSummary | None:
    """功能关闭时返回 None；共享输入失败抛 PortraitUpdateError；单套失败只记录。"""
    if not getattr(config, "portrait_enabled", False):
        return None
    return PortraitUpdateRunner(config, client, catalog_dir).run(
        version=version,
        decoded_lua=decoded_lua,
        staging=staging,
        characters=characters,
        arts_category=arts_category,
    )


def _canonical_sha(payload: dict) -> str:
    text = json.dumps(payload, sort_keys=True, ensure_ascii=False, separators=(",", ":"))
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _write_json_atomic(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + ".part")
    temporary.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2, default=str), encoding="utf-8"
    )
    temporary.replace(path)


def _copy_atomic(source: Path, destination: Path) -> None:
    destination.parent.mkdir(parents=True, exist_ok=True)
    temporary = destination.with_name(destination.name + ".part")
    shutil.copy2(source, temporary)
    temporary.replace(destination)


def portrait_work_manifest_path(config: ServerConfig, version: int) -> Path:
    """工作 manifest 路径；processor 的同版本恢复门控与 runner 使用同一位置。"""
    work_root = (
        Path(config.portrait_work_dir)
        if config.portrait_work_dir
        else Path(config.data_dir) / "portrait_work"
    )
    return work_root / str(version) / "manifest.json"


def unfinished_work_entries(
    work_manifest_path: Path, published_manifest_path: Path | None = None
) -> list[dict]:
    """同版本恢复门控：返回仍有恢复工作的条目。

    work manifest 逐套持久化，恢复中若再次中断会被截断为前缀、后缀条目
    可能整体缺失，因此不能只依赖它；同时检查当前已发布版本
    ``portraits/manifest.json`` 中的 failed/stale。仅读本地文件，
    供 processor 在拉取 CDN 之前判断；无待恢复项时不落 CDN、不写任何状态。
    """
    pending: list[dict] = []
    seen: set[str] = set()

    def _collect(payload: dict, *, check_control_fields: bool) -> None:
        skins = payload.get("skins")
        if not isinstance(skins, list):
            return
        for entry in skins:
            if not isinstance(entry, dict):
                continue
            skin_id = str(entry.get("skin_id"))
            if skin_id in seen:
                continue
            state = str(entry.get("state"))
            if state in ("failed", "stale") or (
                check_control_fields
                and state == "published"
                and (not entry.get("staged") or not entry.get("event_emitted"))
            ):
                seen.add(skin_id)
                pending.append(entry)

    try:
        work_payload = json.loads(Path(work_manifest_path).read_text(encoding="utf-8"))
    except (OSError, ValueError, TypeError):
        work_payload = None
    if isinstance(work_payload, dict):
        _collect(work_payload, check_control_fields=True)
    if published_manifest_path is not None:
        try:
            published_payload = json.loads(Path(published_manifest_path).read_text(encoding="utf-8"))
        except (OSError, ValueError, TypeError):
            published_payload = None
        # 已发布 manifest 已剥掉控制字段：只按 failed/stale 判定，published 视为完成
        if isinstance(published_payload, dict):
            _collect(published_payload, check_control_fields=False)
    return pending


class PortraitUpdateRunner:
    def __init__(self, config: ServerConfig, client: CdnClient, catalog_dir: Path):
        self.config = config
        self.client = client
        self.catalog_dir = Path(catalog_dir)
        self.data_dir = Path(config.data_dir)
        self.work_root = Path(config.portrait_work_dir or (self.data_dir / "portrait_work"))
        self.outbox_root = Path(config.portrait_outbox_dir or (self.data_dir / "portrait_outbox"))
        self.versions_dir = self.data_dir / "versions"
        self.events_path = self.data_dir / "update_events.jsonl"
        self._version_key = ""

    # ---- 主流程 -------------------------------------------------------------

    def run(
        self,
        *,
        version: int,
        decoded_lua: Path,
        staging: Path,
        characters: dict,
        arts_category,
    ) -> PortraitSummary:
        started = time.perf_counter()
        self._version_key = str(version)
        version_key = self._version_key
        work_dir = self.work_root / version_key
        artifacts_dir = work_dir / "artifacts"
        pool_dir = work_dir / "pool"
        skins_root = work_dir / "skins"
        for path in (artifacts_dir, pool_dir / "bundles", skins_root):
            path.mkdir(parents=True, exist_ok=True)
        self._cleanup_other_versions(work_dir)

        basefashion = self._resolve_decoded_file(Path(decoded_lua), "basefashion.lua")
        if basefashion is None:
            raise PortraitUpdateError(f"decoded Lua 缺少 BaseFashion.lua: {decoded_lua}")
        lua_config = self._load_lua_config()
        lua_store = work_dir / "lua" / version_key
        lua_store.mkdir(parents=True, exist_ok=True)
        shutil.copy2(basefashion, lua_store / "BaseFashion.lua")
        self._lua_store = lua_store
        # 有效配置皮肤 = 配置了 show_spine 的展示皮肤；战斗小人等无 show_spine 条目排除。
        roots = [
            root
            for root in lua_config.load_portrait_roots(work_dir / "lua", int(version_key))
            if root.unresolved_reason != "missing_show_spine"
        ]

        catalog = self._load_arts_catalog(arts_category)
        previous, previous_version = self._load_previous_manifest(version_key)
        self._previous_manifest = previous
        self._previous_version = previous_version
        tasks = self._plan_tasks(roots, catalog, characters, previous)
        tasks = self._merge_resume(tasks, self._load_work_entries(work_dir / "manifest.json"), artifacts_dir)

        render_tasks = [task for task in tasks if task.action == "render"]
        tool_error = self._preflight_error() if render_tasks else None
        if tool_error:
            LOGGER.error("stage=portraits version=%s status=failed reason=%s", version_key, tool_error)

        staging_portraits = Path(staging) / "portraits"
        self._save_basefashion(basefashion, staging_portraits)
        manifest_entries: list[dict] = []
        counts = {"success": 0, "failed": 0, "skipped": 0}
        for task in tasks:  # 保持 roots 的配置处理顺序，不按 ordinal 全局重排
            try:
                if task.action == "render" and tool_error is not None:
                    entry = self._failed_entry(task, tool_error, staging_portraits, artifacts_dir)
                    counts["failed"] += 1
                elif task.action == "render":
                    entry = self._render_one(task, pool_dir, skins_root, staging_portraits, artifacts_dir)
                    counts["success"] += 1
                elif task.action == "inherit":
                    entry = self._stage_inherited(task, staging_portraits, artifacts_dir)
                    counts["skipped"] += 1
                elif task.action == "done":
                    entry = self._restage_done(task, staging_portraits, artifacts_dir)
                    counts["skipped"] += 1
                elif task.action == "emit":
                    entry = self._emit_pending_event(task, staging_portraits, artifacts_dir)
                    counts["success"] += 1
                else:  # skip
                    entry = self._skipped_entry(task)
                    counts["skipped"] += 1
            except Exception as error:  # 单套失败不拖垮角色主流程
                LOGGER.exception(
                    "stage=portrait_render version=%s skin=%s status=failed",
                    version_key,
                    task.skin_id,
                )
                entry = self._failed_entry(task, f"{type(error).__name__}: {error}", staging_portraits, artifacts_dir)
                counts["failed"] += 1
            manifest_entries.append(entry)
            # 逐套持久化只写 work manifest；staging/已发布 manifest 在整轮结束后
            # 一次性写，恢复中任何中断都不截断当前已发布 manifest。
            work_manifest_path = work_dir / "manifest.json"
            self._persist_work_manifest(work_manifest_path, version_key, manifest_entries)
            # portrait_ready 必须落在 work manifest(event_emitted=False) 之后：
            # 事件前崩溃 → 恢复走 emit 直接补发，不重渲；事件后崩溃 → 重复事件
            # 由 Bot 幂等键去重。继承/已完成条目不补发。
            if (entry.get("state") == "published" and not entry.get("event_emitted")
                    and str(entry.get("source_version")) == version_key):
                self._append_ready_event(entry)
                entry["event_emitted"] = True
                self._persist_work_manifest(work_manifest_path, version_key, manifest_entries)

        if manifest_entries:
            self._write_staging_manifest(staging_portraits, version_key, manifest_entries)
        shutil.rmtree(skins_root, ignore_errors=True)
        LOGGER.info(
            "stage=portraits version=%s elapsed=%.2fs success=%d failed=%d skipped=%d",
            version_key,
            time.perf_counter() - started,
            counts["success"],
            counts["failed"],
            counts["skipped"],
        )
        return PortraitSummary(
            success=counts["success"], failed=counts["failed"], skipped=counts["skipped"]
        )

    # ---- 计划 ---------------------------------------------------------------

    def _resolve_decoded_file(self, decoded_lua: Path, expected_name: str) -> Path | None:
        if not decoded_lua.is_dir():
            return None
        expected = expected_name.casefold()
        return next(
            (
                path
                for path in decoded_lua.iterdir()
                if path.is_file() and path.name.casefold() == expected
            ),
            None,
        )

    @staticmethod
    def _save_basefashion(source: Path, staging_portraits: Path) -> None:
        """把解码后的 BaseFashion.lua 随版本保存，供同版本恢复免整套 Data 重下载。

        原子写入；目标已存在同内容文件时不重写、不报错——同版本恢复时
        staging 即当前版本目录，保存的 Lua 与输入逐字节一致。
        """
        destination = Path(staging_portraits) / "BaseFashion.lua"
        data = Path(source).read_bytes()
        try:
            if destination.is_file() and destination.read_bytes() == data:
                return
        except OSError:
            pass
        temporary = destination.with_name(destination.name + ".part")
        temporary.parent.mkdir(parents=True, exist_ok=True)
        temporary.write_bytes(data)
        temporary.replace(destination)

    def _tool_dir(self) -> Path:
        return Path(self.config.portrait_tool_dir) if self.config.portrait_tool_dir else _tool_app_root()

    def _load_lua_config(self):
        tool_dir = self._tool_dir()
        if not tool_dir.is_dir():
            raise PortraitUpdateError(f"Tool 源码目录不存在: {tool_dir}")
        if str(tool_dir) not in sys.path:
            sys.path.insert(0, str(tool_dir))
        try:
            from app.features.preview.portrait_config import lua_config
        except Exception as error:
            raise PortraitUpdateError(f"无法载入 Tool 立绘配置解析器: {error}") from error
        return lua_config

    def _load_arts_catalog(self, arts_category):
        if arts_category is None:
            raise PortraitUpdateError("版本清单缺少 Arts 分类，无法选择立绘资源")
        local_catalog = self.catalog_dir / f"arts_{arts_category.hash}.bundle"
        if not local_catalog.is_file():
            self.client.download_category_catalog("Arts", arts_category.hash, local_catalog)
        try:
            return read_catalog(local_catalog, self.config.assetbundle_key)
        except Exception as error:
            raise PortraitUpdateError(f"Arts 分类清单读取失败: {error}") from error

    def _load_previous_manifest(self, version_key: str) -> tuple[dict | None, str | None]:
        if not self.versions_dir.is_dir():
            return None, None
        candidates = []
        for directory in self.versions_dir.iterdir():
            if not directory.is_dir() or directory.name == version_key:
                continue
            if not directory.name.isdigit():
                continue
            if (directory / "portraits" / "manifest.json").is_file():
                candidates.append(int(directory.name))
        if not candidates:
            return None, None
        chosen = str(max(candidates))
        path = self.versions_dir / chosen / "portraits" / "manifest.json"
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
            skins = payload.get("skins")
            if not isinstance(skins, list):
                raise ValueError("skins 缺失")
        except (OSError, ValueError, TypeError, KeyError) as error:
            LOGGER.warning("stage=portraits status=skipped reason=invalid_previous_manifest version=%s error=%s", chosen, error)
            return None, None
        return payload, chosen

    def _previous_artifact(self, previous: dict, previous_version: str) -> Path | None:
        artifact_path = str(previous.get("artifact_path") or "")
        sha = str(previous.get("artifact_sha256") or "")
        if not artifact_path or not sha:
            return None
        path = self.versions_dir / previous_version / "portraits" / artifact_path
        try:
            if not path.is_file():
                return None
            if hashlib.sha256(path.read_bytes()).hexdigest() != sha:
                return None
        except OSError:
            return None
        return path

    def _plan_tasks(self, roots, catalog, characters, previous) -> list[_SkinTask]:
        previous_version = None
        prev_skins: dict[str, dict] = {}
        if previous is not None:
            previous_version = str(previous.get("version"))
            prev_skins = {str(entry.get("skin_id")): entry for entry in previous.get("skins", [])}
        tasks = []
        ordinals: dict[str, int] = {}
        for root in roots:
            metadata = dict(root.metadata)
            card_id = str(metadata.get("card_id", "") or "")
            # 每个角色按全部有效配置皮肤的游戏顺序从 1 独立编号
            ordinal = ordinals.get(card_id, 0) + 1
            ordinals[card_id] = ordinal
            entry_id = str(metadata.get("entry_id", root.skin_id))
            character = characters.get(card_id) if card_id else None
            name = ""
            if isinstance(character, dict):
                name = str(character.get("name", "")).split("/", 1)[0]
            prefab_name = str(root.prefab_name or "")
            matches = select_portrait_prefab_assets(catalog, prefab_name) if prefab_name else ()
            bundle_hashes = bundle_hashes_for_assets(catalog, matches)
            config_fp = _canonical_sha(
                {
                    "skin_id": str(root.skin_id),
                    "prefab": prefab_name,
                    "position": list(root.position) if root.position else None,
                    "card_id": card_id,
                    "entry_id": entry_id,
                }
            )
            fingerprint = _canonical_sha(
                {"v": 1, "config": config_fp, "resources": sorted(bundle_hashes)}
            )
            prev = prev_skins.get(str(root.skin_id))
            if previous is None:
                # 首次无基线：只记录当前指纹，不自动渲染历史
                action = "skip"
            elif prev is None or prev.get("input_fingerprint") != fingerprint:
                action = "render"  # 新角色全部皮肤 / 老角色新增皮肤 / 输入变化
            elif str(prev.get("state")) == "published":
                artifact = (
                    self._previous_artifact(prev, previous_version)
                    if previous_version
                    else None
                )
                action = "inherit" if artifact is not None else "render"
            elif str(prev.get("state")) == "not_generated":
                action = "skip"  # 指纹未变且无历史产物，保持不补历史
            else:  # failed / stale 在本版本指纹一致时重试
                action = "render"
            tasks.append(
                _SkinTask(
                    skin_id=str(root.skin_id),
                    character_id=card_id,
                    ordinal=ordinal,
                    name=name,
                    prefab_name=prefab_name,
                    bundle_hashes=bundle_hashes,
                    fingerprint=fingerprint,
                    action=action,
                    previous=prev,
                )
            )
        return tasks

    def _load_work_entries(self, path: Path) -> list[dict]:
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError, TypeError):
            return []
        skins = payload.get("skins")
        return [entry for entry in skins if isinstance(entry, dict)] if isinstance(skins, list) else []

    def _merge_resume(self, tasks: list[_SkinTask], work_entries: list[dict], artifacts_dir: Path) -> list[_SkinTask]:
        work_by_skin = {str(entry.get("skin_id")): entry for entry in work_entries}
        # 同版本恢复时当前已发布 manifest 必存在；work 被中断截断为前缀时，
        # 缺失的已完成项从它播种，保证恢复不缺后缀、不重渲、不重复发事件。
        published_by_skin = self._load_published_skins(self._version_key)
        emitted_skins = self._load_emitted_event_skins() if published_by_skin else frozenset()
        for task in tasks:
            work_entry = work_by_skin.get(task.skin_id)
            if work_entry is None or work_entry.get("input_fingerprint") != task.fingerprint:
                self._seed_from_published(task, published_by_skin, emitted_skins)
                continue
            if str(work_entry.get("state")) != "published":
                continue  # failed / stale 留在 manifest，重试渲染
            if not work_entry.get("staged") or not work_entry.get("artifact_path"):
                continue
            artifact = self._work_artifact_path(artifacts_dir, work_entry)
            sha = str(work_entry.get("artifact_sha256") or "")
            try:
                if not artifact.is_file() or (
                    sha and hashlib.sha256(artifact.read_bytes()).hexdigest() != sha
                ):
                    continue
            except OSError:
                continue
            task.work_entry = work_entry
            if str(work_entry.get("source_version")) != self._version_key:
                task.action = "done"  # 继承产物：永不补发事件
            elif work_entry.get("event_emitted"):
                task.action = "done"
            else:
                task.action = "emit"  # 崩溃在暂存之后、事件之前
        return tasks

    def _load_published_skins(self, version_key: str) -> dict[str, dict] | None:
        """当前已发布版本的 portraits manifest（同版本恢复时存在），按 skin_id 索引。"""
        path = self.versions_dir / version_key / "portraits" / "manifest.json"
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError, TypeError):
            return None
        skins = payload.get("skins")
        if not isinstance(skins, list):
            return None
        return {str(entry.get("skin_id")): entry for entry in skins if isinstance(entry, dict)}

    def _load_emitted_event_skins(self) -> frozenset[str]:
        """事件流里本版本已发 portrait_ready 的皮肤，供播种时判定是否还需补发。"""
        try:
            lines = self.events_path.read_text(encoding="utf-8").splitlines()
        except (OSError, ValueError, TypeError):
            return frozenset()
        skins: set[str] = set()
        for line in lines:
            try:
                event = json.loads(line)
            except ValueError:
                continue
            if (
                isinstance(event, dict)
                and event.get("event") == "portrait_ready"
                and str(event.get("version")) == self._version_key
                and event.get("skin_id") is not None
            ):
                skins.add(str(event["skin_id"]))
        return frozenset(skins)

    def _seed_from_published(self, task: _SkinTask, published_by_skin: dict | None,
                             emitted_skins: frozenset[str]) -> None:
        """work manifest 截断缺失的条目用已发布 manifest 播种为 done/emit。

        只接管仍与本版本指纹一致、产物在位且哈希吻合的 published 项；
        failed/stale 与指纹漂移项保持规划动作（重试渲染）。
        """
        if not published_by_skin:
            return
        entry = published_by_skin.get(task.skin_id)
        if (
            entry is None
            or entry.get("input_fingerprint") != task.fingerprint
            or str(entry.get("state")) != "published"
            or not entry.get("artifact_path")
        ):
            return
        artifact = self.versions_dir / self._version_key / "portraits" / str(entry["artifact_path"])
        sha = str(entry.get("artifact_sha256") or "")
        try:
            if not artifact.is_file() or (
                sha and hashlib.sha256(artifact.read_bytes()).hexdigest() != sha
            ):
                return
        except OSError:
            return
        seeded = dict(entry)
        seeded["staged"] = True
        seeded["event_emitted"] = task.skin_id in emitted_skins
        task.work_entry = seeded
        if str(seeded.get("source_version")) != self._version_key or seeded["event_emitted"]:
            task.action = "done"  # 继承产物或已发事件：永不补发
        else:
            task.action = "emit"  # 本版本产物但事件缺失：崩溃在暂存之后、事件之前

    @staticmethod
    def _work_artifact_path(artifacts_dir: Path, entry: dict) -> Path:
        name = f"{entry.get('character_id')}_{entry.get('skin_id')}.png"
        return Path(artifacts_dir) / name

    def _preflight_error(self) -> str | None:
        checks = (
            ("portrait_tool_dir", self._tool_dir(), False),
            ("portrait_dotnet_path", self.config.portrait_dotnet_path, True),
            ("portrait_scene_cli_path", self.config.portrait_scene_cli_path, True),
            ("portrait_assetstudio_cli", self.config.portrait_assetstudio_cli, True),
        )
        missing = []
        for name, value, must_be_file in checks:
            if value is None:
                missing.append(name)
                continue
            path = Path(value)
            if must_be_file and not path.is_file():
                missing.append(name)
            if not must_be_file and not path.is_dir():
                missing.append(name)
        if missing:
            return "立绘工具缺失或未配置: " + ", ".join(missing)
        return None

    def _cleanup_other_versions(self, work_dir: Path) -> None:
        if not self.work_root.is_dir():
            return
        removed = 0
        for directory in self.work_root.iterdir():
            if directory.is_dir() and directory != work_dir:
                shutil.rmtree(directory, ignore_errors=True)
                removed += 1
        if removed:
            LOGGER.info("stage=portraits status=cleaned removed_workdirs=%d", removed)

    # ---- 执行 ---------------------------------------------------------------

    def _ensure_pool_bundles(self, task: _SkinTask, pool_dir: Path) -> bool:
        bundles_dir = pool_dir / "bundles"
        added = False
        for bundle_hash in task.bundle_hashes:
            destination = bundles_dir / f"{bundle_hash}.bundle"
            if not destination.is_file():
                self._download_bundle(bundle_hash, destination)
                added = True
        return added

    def _download_bundle(self, bundle_hash: str, destination: Path) -> Path:
        path = Path(self.client.download_bundle(bundle_hash, destination))
        repair_bundle_header(path)  # 只处理下载副本，原始 CDN 缓存不受影响
        return path

    def _load_render_process(self):
        """载入 Tool 受控外部进程能力，map 与渲染共用资源限制语义。"""
        tool_dir = self._tool_dir()
        if not tool_dir.is_dir():
            raise PortraitUpdateError(f"Tool 源码目录不存在: {tool_dir}")
        if str(tool_dir) not in sys.path:
            sys.path.insert(0, str(tool_dir))
        try:
            from app.platform.portrait_render_process import (
                RenderProcessLimits,
                run_render_process,
            )
        except Exception as error:
            raise PortraitUpdateError(f"无法载入 Tool 受控进程模块: {error}") from error
        return RenderProcessLimits, run_render_process

    @staticmethod
    def _log_tail(path, limit: int = 300) -> str:
        try:
            text = Path(path).read_text(encoding="utf-8", errors="replace")
        except (OSError, TypeError, ValueError):
            return ""
        return text.strip()[-limit:]

    def _build_assets_map(self, bundle_dir: Path, map_path: Path) -> None:
        cli = self.config.portrait_assetstudio_cli
        if cli is None:
            raise PortraitUpdateError("未配置 portraits.assetstudio_cli，无法生成资源映射")
        limits_cls, run_render_process = self._load_render_process()
        limits = limits_cls(
            memory_max_bytes=self.config.portrait_memory_max_bytes,
            memory_high_bytes=self.config.portrait_memory_high_bytes,
            cpu_affinity=tuple(self.config.portrait_cpu_affinity),
            timeout_seconds=self.config.portrait_map_timeout_seconds,
        )
        command = []
        if self.config.portrait_assetstudio_dotnet is not None:
            command.append(str(self.config.portrait_assetstudio_dotnet))
        command.extend(
            [str(cli), str(bundle_dir), str(map_path.parent), *_AS_MAP_ARGS]
        )
        map_work_dir = Path(map_path).parent / "assetstudio_map"
        result = run_render_process(
            command,
            work_dir=map_work_dir,
            env=dict(os.environ),
            limits=limits,
        )
        stderr_tail = self._log_tail(getattr(result, "stderr_path", None))
        if result.timed_out:
            raise PortraitUpdateError(
                f"AssetStudio map 生成超时 ({self.config.portrait_map_timeout_seconds}s)"
                f"；证据目录: {map_work_dir}"
                + (f"；stderr 尾部: {stderr_tail}" if stderr_tail else "")
            )
        if result.returncode != 0:
            detail = stderr_tail or self._log_tail(getattr(result, "stdout_path", None))
            raise PortraitUpdateError(
                f"AssetStudio map 生成失败 (exit {result.returncode})"
                + (f": {detail}" if detail else f"；证据目录: {map_work_dir}")
            )
        if not map_path.is_file() or map_path.stat().st_size <= 100:
            raise PortraitUpdateError(
                "AssetStudio map 输出为空"
                + (f"；stderr 尾部: {stderr_tail}" if stderr_tail else f"；证据目录: {map_work_dir}")
            )

    def _assemble_workspace(self, task: _SkinTask, pool_dir: Path, skins_root: Path) -> Path:
        # Task 2 契约要求 workspace/output/work 互不嵌套，故使用平级目录。
        base = Path(skins_root) / task.skin_id
        workspace = base / "workspace"
        lua_dir = workspace / "lua" / self._version_key
        lua_dir.mkdir(parents=True, exist_ok=True)
        shutil.copy2(self._lua_store / "BaseFashion.lua", lua_dir / "BaseFashion.lua")
        bundles_dir = workspace / "bundles" / self._version_key
        bundles_dir.mkdir(parents=True, exist_ok=True)
        for bundle_hash in task.bundle_hashes:
            source = pool_dir / "bundles" / f"{bundle_hash}.bundle"
            target = bundles_dir / f"{bundle_hash}.bundle"
            try:
                os.link(source, target)
            except OSError:
                shutil.copy2(source, target)
        shutil.copy2(pool_dir / "assets_map.json", workspace / "assets_map.json")
        (base / "output").mkdir(parents=True, exist_ok=True)
        (base / "work").mkdir(parents=True, exist_ok=True)
        return base

    def _run_render_job(self, task: _SkinTask, workspace: Path) -> dict:
        """调用 Task 2 受控入口（每套一个受控子进程）。

        ``workspace`` 是套装工作目录，内含平级的 ``workspace/``（Task 2 输入）、
        ``output/``、``work/``；result 必须位于 work_root 内（入口强制校验）。
        """
        if not task.character_id:
            raise ValueError(f"BaseFashion 条目缺少 card_id: skin {task.skin_id}")
        input_root = Path(workspace) / "workspace"
        request = {
            "schema_version": 2,
            "job_id": f"portrait-{self._version_key}-{task.skin_id}",
            "version": self._version_key,
            "character_id": task.character_id,
            "skin_id": task.skin_id,
            "profile": {"format": "png", "scale": 2, "scope": "full"},
            "workspace_root": str(input_root),
            "output_root": str(Path(workspace) / "output"),
            "work_root": str(Path(workspace) / "work"),
            "dotnet_path": str(self.config.portrait_dotnet_path),
            "scene_cli_path": str(self.config.portrait_scene_cli_path),
            "png_working_rows": self.config.portrait_png_working_rows,
            "limits": {
                "memory_max_bytes": self.config.portrait_memory_max_bytes,
                "memory_high_bytes": self.config.portrait_memory_high_bytes,
                "cpu_affinity": list(self.config.portrait_cpu_affinity),
                "timeout_seconds": self.config.portrait_timeout_seconds,
            },
        }
        if self.config.portrait_query_cli_path is not None:
            request["query_cli_path"] = str(self.config.portrait_query_cli_path)
        if self.config.portrait_xvfb_run_path is not None:
            request["xvfb_run_path"] = str(self.config.portrait_xvfb_run_path)
        request_path = Path(workspace) / "request.json"
        result_path = Path(workspace) / "work" / "result.json"
        _write_json_atomic(request_path, request)
        tool_dir = self._tool_dir()
        env = dict(os.environ)
        python_path = str(tool_dir)
        if env.get("PYTHONPATH"):
            python_path = python_path + os.pathsep + env["PYTHONPATH"]
        env["PYTHONPATH"] = python_path
        python_bin = self.config.portrait_python_bin or sys.executable
        command = [
            python_bin,
            "-m",
            "app.features.preview.portrait_headless",
            "--request",
            str(request_path),
            "--result",
            str(result_path),
        ]
        try:
            process = subprocess.Popen(
                command,
                cwd=str(tool_dir),
                env=env,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                start_new_session=_job_start_new_session(),
            )
        except OSError as error:
            raise ValueError(f"渲染入口子进程无法启动: {error}") from error
        try:
            _stdout, stderr = process.communicate(timeout=self.config.portrait_job_timeout_seconds)
        except subprocess.TimeoutExpired as error:
            _terminate_job_process(process)
            partial_stderr = getattr(error, "stderr", None)
            detail = ""
            if isinstance(partial_stderr, bytes) and partial_stderr:
                detail = "；stderr 尾部: " + partial_stderr.decode("utf-8", errors="replace").strip()[-300:]
            raise ValueError(
                f"渲染入口子进程超时 ({self.config.portrait_job_timeout_seconds}s)" + detail
            ) from error
        if not result_path.is_file():
            detail = stderr.decode("utf-8", errors="replace")[:300] if stderr else ""
            raise ValueError(f"渲染入口未写出结果 (exit {process.returncode}): {detail}")
        try:
            result = json.loads(result_path.read_text(encoding="utf-8-sig"))
        except (OSError, ValueError) as error:
            raise ValueError(f"渲染结果不是合法 JSON: {error}") from error
        if not isinstance(result, dict):
            raise ValueError("渲染结果不是对象")
        return result

    def _validate_rendered_png(self, result: dict) -> dict:
        if result.get("status") != "success":
            diagnostics = result.get("diagnostics")
            detail = "; ".join(str(item) for item in diagnostics) if isinstance(diagnostics, list) else str(result.get("status"))
            raise ValueError(f"渲染失败: {detail}")
        artifact = Path(str(result.get("artifact_path") or ""))
        if not artifact.is_file():
            raise ValueError(f"渲染结果缺少产物文件: {artifact}")
        _ensure_png_within_limit(artifact)  # stat 先行：超限即拒，不把大文件读进内存
        data = artifact.read_bytes()
        if not data.startswith(PNG_SIGNATURE) or len(data) < 33:
            raise ValueError("渲染产物不是有效 PNG")
        width, height = struct.unpack(">II", data[16:24])
        try:
            expected = (int(result.get("width")), int(result.get("height")))
        except (TypeError, ValueError):
            raise ValueError("渲染证据缺少尺寸") from None
        if (width, height) != expected:
            raise ValueError("PNG 尺寸与渲染证据不一致")
        sha256 = hashlib.sha256(data).hexdigest()
        if sha256 != result.get("sha256"):
            raise ValueError("PNG 哈希与渲染证据不一致")
        return {"sha256": sha256, "width": width, "height": height}

    # ---- 产物与事件 -----------------------------------------------------------

    def _render_one(self, task: _SkinTask, pool_dir: Path, skins_root: Path,
                    staging_portraits: Path, artifacts_dir: Path) -> dict:
        added = self._ensure_pool_bundles(task, pool_dir)
        if added or not (pool_dir / "assets_map.json").is_file():
            self._build_assets_map(pool_dir / "bundles", pool_dir / "assets_map.json")
        workspace = self._assemble_workspace(task, pool_dir, skins_root)
        try:
            result = self._run_render_job(task, workspace)
            evidence = self._validate_rendered_png(result)
            return self._publish_rendered(task, Path(str(result.get("artifact_path"))), evidence,
                                          staging_portraits, artifacts_dir)
        finally:
            shutil.rmtree(workspace, ignore_errors=True)

    def _publish_rendered(self, task: _SkinTask, artifact: Path, evidence: dict,
                          staging_portraits: Path, artifacts_dir: Path) -> dict:
        """PNG/staging/outbox 落盘并构造 published 条目；事件由 run 循环在
        work manifest(event_emitted=False) 持久化之后再追加。"""
        _ensure_png_within_limit(artifact)
        relative = f"{task.character_id}/{task.skin_id}.png"
        _copy_atomic(artifact, staging_portraits / relative)
        _copy_atomic(artifact, artifacts_dir / f"{task.character_id}_{task.skin_id}.png")
        self._write_outbox(task, evidence["sha256"], artifact)
        entry = self._base_entry(task)
        entry.update(
            state="published",
            artifact_path=relative,
            artifact_sha256=evidence["sha256"],
            source_version=int(self._version_key),
            width=evidence["width"],
            height=evidence["height"],
            staged=True,
        )
        return entry

    def _stage_inherited(self, task: _SkinTask, staging_portraits: Path, artifacts_dir: Path) -> dict:
        previous = task.previous or {}
        source = self._previous_artifact(previous, self._previous_version) if self._previous_version else None
        if source is None:
            raise ValueError(f"继承产物缺失: skin {task.skin_id}")
        relative = str(previous.get("artifact_path"))
        _copy_atomic(source, staging_portraits / relative)
        _copy_atomic(source, artifacts_dir / f"{task.character_id}_{task.skin_id}.png")
        entry = self._base_entry(task)
        entry.update(
            state="published",
            artifact_path=relative,
            artifact_sha256=previous.get("artifact_sha256"),
            source_version=previous.get("source_version"),
            width=previous.get("width"),
            height=previous.get("height"),
            staged=True,
        )
        return entry

    def _resume_artifact(self, work_entry: dict, artifacts_dir: Path) -> Path:
        """恢复产物优先取 work 副本；缺失或哈希不符时回退到当前已发布版本目录。"""
        sha = str(work_entry.get("artifact_sha256") or "")
        candidates = [self._work_artifact_path(artifacts_dir, work_entry)]
        relative = str(work_entry.get("artifact_path") or "")
        if relative:
            candidates.append(self.versions_dir / self._version_key / "portraits" / relative)
        for candidate in candidates:
            try:
                if not candidate.is_file():
                    continue
                if sha and hashlib.sha256(candidate.read_bytes()).hexdigest() != sha:
                    continue
            except OSError:
                continue
            return candidate
        raise ValueError(f"恢复产物缺失: skin {work_entry.get('skin_id')}")

    def _restage_done(self, task: _SkinTask, staging_portraits: Path, artifacts_dir: Path) -> dict:
        work_entry = dict(task.work_entry or {})
        artifact = self._resume_artifact(work_entry, artifacts_dir)
        _copy_atomic(artifact, staging_portraits / str(work_entry.get("artifact_path")))
        work_entry["staged"] = True
        return work_entry

    def _emit_pending_event(self, task: _SkinTask, staging_portraits: Path, artifacts_dir: Path) -> dict:
        work_entry = dict(task.work_entry or {})
        artifact = self._resume_artifact(work_entry, artifacts_dir)
        _ensure_png_within_limit(artifact)  # 补发事件前同样守 QQ 20MiB 上限
        _copy_atomic(artifact, staging_portraits / str(work_entry.get("artifact_path")))
        self._write_outbox(task, str(work_entry.get("artifact_sha256")), artifact)
        self._append_ready_event(work_entry)
        work_entry["event_emitted"] = True
        work_entry["staged"] = True
        return work_entry

    def _skipped_entry(self, task: _SkinTask) -> dict:
        entry = self._base_entry(task)
        entry["state"] = "not_generated"
        return entry

    def _failed_entry(self, task: _SkinTask, message: str,
                      staging_portraits: Path, artifacts_dir: Path) -> dict:
        previous = task.previous or {}
        source = (
            self._previous_artifact(previous, self._previous_version)
            if self._previous_version and str(previous.get("state")) in ("published", "stale")
            else None
        )
        entry = self._base_entry(task)
        entry["error"] = str(message)[:300]
        if source is not None:
            # 旧图保留并标旧：输入已变化或本轮渲染失败，均随版本发布旧产物
            relative = str(previous.get("artifact_path"))
            _copy_atomic(source, staging_portraits / relative)
            _copy_atomic(source, artifacts_dir / f"{task.character_id}_{task.skin_id}.png")
            entry.update(
                state="stale",
                artifact_path=relative,
                artifact_sha256=previous.get("artifact_sha256"),
                source_version=previous.get("source_version"),
                width=previous.get("width"),
                height=previous.get("height"),
                staged=True,
            )
        else:
            entry["state"] = "failed"
        return entry

    # ---- 基础字段 ------------------------------------------------------------

    def _base_entry(self, task: _SkinTask) -> dict:
        return {
            "character_id": task.character_id,
            "skin_id": task.skin_id,
            "ordinal": task.ordinal,
            "name": task.name,
            "input_fingerprint": task.fingerprint,
            "state": "failed",
            "artifact_path": None,
            "artifact_sha256": None,
            "source_version": None,
            "width": None,
            "height": None,
            "error": None,
            "event_emitted": False,
            "staged": False,
        }

    def _write_outbox(self, task: _SkinTask, sha256: str, artifact: Path) -> None:
        filename = f"{task.character_id}_{task.skin_id}_{sha256[:12]}.png"
        _copy_atomic(artifact, self.outbox_root / self._version_key / filename)

    def _append_ready_event(self, entry: dict) -> None:
        sha256 = str(entry.get("artifact_sha256") or "")
        filename = f"{entry.get('character_id')}_{entry.get('skin_id')}_{sha256[:12]}.png"
        payload = {
            "event": "portrait_ready",
            "version": self._version_key,
            "character_id": entry.get("character_id"),
            "skin_id": entry.get("skin_id"),
            "ordinal": entry.get("ordinal"),
            "name": entry.get("name"),
            "path": f"portrait_outbox/{self._version_key}/{filename}",
            "sha256": sha256,
            "width": entry.get("width"),
            "height": entry.get("height"),
            "at": datetime.now().astimezone().isoformat(),
        }
        self.events_path.parent.mkdir(parents=True, exist_ok=True)
        with self.events_path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(payload, ensure_ascii=False) + "\n")

    def _persist_work_manifest(self, work_manifest_path: Path, version_key: str,
                               entries: list[dict]) -> None:
        """逐套持久化 work manifest（含控制字段），供同版本恢复；被中断截断为前缀无害。"""
        _write_json_atomic(
            work_manifest_path,
            {"schema_version": MANIFEST_SCHEMA_VERSION, "version": version_key, "skins": entries},
        )

    def _write_staging_manifest(self, staging_portraits: Path, version_key: str,
                                entries: list[dict]) -> None:
        """完整一轮结束后原子写 staging/已发布 manifest，剥掉 work 控制字段。"""
        _write_json_atomic(
            staging_portraits / "manifest.json",
            {
                "schema_version": MANIFEST_SCHEMA_VERSION,
                "version": version_key,
                "skins": [
                    {key: value for key, value in entry.items() if key not in _CONTROL_FIELDS}
                    for entry in entries
                ],
            },
        )
