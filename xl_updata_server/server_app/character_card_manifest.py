"""每版本图鉴有效产物的 card manifest（schema 1）。

每个已发布版本在 ``character_cards/manifest.json`` 记录当版每个有效角色：

- ``character_id``：角色身份键，继承/改名/删除都按它判断；
- ``state``：``published``（当版或更早渲染的有效内容）/ ``stale``
  （内容已变化但重渲失败，仍指向旧内容，不得伪装成新修订）；
- ``artifact_path``：相对版本根的路径（含 ``character_cards/`` 前缀）；
- ``artifact_sha256``：实际 PNG 字节的 SHA-256；
- ``data_sha256``：角色全字段规范化 JSON 的 SHA-256，与
  ``processor._canonical_character`` 同口径，是内容修订链的识别键；
- ``source_version``：实际生成此内容的游戏版本；
- ``error``：stale 原因（渲染错误），否则为 None。

旧的版本目录没有 card manifest 时，由 ``legacy_manifest_from_version``
按实际 PNG 与角色数据补建（全部视为当版发布）；正常更新的查询回退不在本模块。
"""

from __future__ import annotations

import hashlib
import json
import logging
import shutil
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any

from .character_card_exporter import CardExportReport
from .extractor import safe_role_name

LOGGER = logging.getLogger(__name__)

SCHEMA_VERSION = 1
MANIFEST_NAME = "manifest.json"


def canonical_character_sha(data: Mapping[str, Any]) -> str:
    """角色全字段规范化 JSON 的 SHA-256（内容修订链的识别键）。"""
    canonical = json.dumps(data, sort_keys=True, ensure_ascii=False, default=str)
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def _artifact_sha(path: Path) -> str:
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def _id_sort_key(character_id: str) -> tuple[int, int | str]:
    if character_id.isdecimal():
        return (0, int(character_id))
    return (1, character_id)


def _relative_artifact(cards_dir: Path, artifact: Path) -> str:
    return f"character_cards/{Path(artifact).name}"


def write_card_manifest(
    cards_dir: Path,
    *,
    version: int | str,
    characters: Mapping[str | int, Mapping[str, Any]],
    card_report: CardExportReport,
    previous_manifest: Mapping[str, Any] | None,
) -> dict[str, Any]:
    """构建当版有效条目并原子写入 ``cards_dir/manifest.json``，返回内容。

    - 出现在 ``card_report.character_outputs`` 的角色：published，
      ``source_version`` 记当前版本，artifact 哈希取实际 PNG 字节；
    - 内容未变化（与上一版 ``data_sha256`` 相同且无新产物）：继承上一版条目，
      保留原 ``source_version`` 与全部哈希；
    - 内容变化但渲染失败（``card_report.failures``）：保留旧有效内容并标
      stale，artifact 与 data 哈希维持旧值；
    - 已删除的角色不产生条目，即使旧 PNG 仍留在目录中。
    """
    cards_dir = Path(cards_dir)
    outputs = {str(character_id): Path(path) for character_id, path in card_report.character_outputs}
    failures = {str(item.character_id): item.error for item in card_report.failures}
    previous_entries: dict[str, Mapping[str, Any]] = {}
    if isinstance(previous_manifest, Mapping):
        raw_entries = previous_manifest.get("characters")
        if isinstance(raw_entries, list):
            previous_entries = {
                str(entry.get("character_id")): entry
                for entry in raw_entries
                if isinstance(entry, Mapping) and entry.get("character_id") is not None
            }

    entries: list[dict[str, Any]] = []
    for raw_id, data in characters.items():
        character_id = str(raw_id)
        record = data if isinstance(data, Mapping) else {}
        data_sha = canonical_character_sha(record)
        previous = previous_entries.get(character_id)
        artifact = outputs.get(character_id)
        if artifact is not None:
            entries.append({
                "character_id": character_id,
                "state": "published",
                "artifact_path": _relative_artifact(cards_dir, artifact),
                "artifact_sha256": _artifact_sha(artifact),
                "data_sha256": data_sha,
                "source_version": str(version),
                "error": None,
            })
            continue
        failure = failures.get(character_id)
        if failure is not None and previous is not None:
            entries.append({
                "character_id": character_id,
                "state": "stale",
                "artifact_path": previous.get("artifact_path"),
                "artifact_sha256": previous.get("artifact_sha256"),
                "data_sha256": previous.get("data_sha256"),
                "source_version": previous.get("source_version"),
                "error": str(failure),
            })
            continue
        if previous is not None and str(previous.get("state")) == "stale":
            # 渲染仍失败/未重渲：继续携带旧内容，角色不得从有效清单中丢失
            entries.append({
                "character_id": character_id,
                "state": "stale",
                "artifact_path": previous.get("artifact_path"),
                "artifact_sha256": previous.get("artifact_sha256"),
                "data_sha256": previous.get("data_sha256"),
                "source_version": previous.get("source_version"),
                "error": previous.get("error"),
            })
            continue
        if previous is not None and previous.get("data_sha256") == data_sha:
            entries.append({
                "character_id": character_id,
                "state": str(previous.get("state") or "published"),
                "artifact_path": previous.get("artifact_path"),
                "artifact_sha256": previous.get("artifact_sha256"),
                "data_sha256": previous.get("data_sha256"),
                "source_version": previous.get("source_version"),
                "error": previous.get("error"),
            })
            continue
        # 无上一版条目且无当版产物：没有可声明的有效内容，跳过。
    entries.sort(key=lambda entry: _id_sort_key(str(entry["character_id"])))
    payload: dict[str, Any] = {
        "schema_version": SCHEMA_VERSION,
        "version": str(version),
        "characters": entries,
    }
    save_card_manifest(cards_dir, payload)
    return payload


def save_card_manifest(cards_dir: Path, payload: Mapping[str, Any]) -> dict[str, Any]:
    """原子写 ``cards_dir/manifest.json``（``.part`` 临时文件 + replace）。"""
    cards_dir = Path(cards_dir)
    cards_dir.mkdir(parents=True, exist_ok=True)
    temporary = cards_dir / f"{MANIFEST_NAME}.part"
    temporary.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2, default=str), encoding="utf-8"
    )
    temporary.replace(cards_dir / MANIFEST_NAME)
    return dict(payload)


def load_card_manifest(cards_dir: Path) -> dict[str, Any] | None:
    """读取 card manifest；缺失或损坏返回 None（调用方决定回退策略）。"""
    path = Path(cards_dir) / MANIFEST_NAME
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError, TypeError):
        return None
    if not isinstance(payload, dict):
        return None
    characters = payload.get("characters")
    if not isinstance(characters, list):
        return None
    return payload


def legacy_manifest_from_version(version_dir: Path, version: int | str) -> dict[str, Any] | None:
    """无 card manifest 的旧版本补建：按角色数据与实际 PNG 生成全 published 清单。

    文件名遵循导出器约定 ``<safe_role_name(id)>_<...>.png``。同一角色有多个
    改名残留候选时，优先取版本根 manifest ``character_cards.outputs`` 明确
    声明的当版产物；仍歧义则跳过该角色并记日志，绝不猜测。找不到对应
    角色数据的 PNG 不参与。无角色数据时返回 None，调用方走更弱的回退。
    """
    version_dir = Path(version_dir)
    try:
        current = json.loads((version_dir / "character_data" / "current.json").read_text(encoding="utf-8"))
    except (OSError, ValueError, TypeError):
        return None
    characters = current.get("characters") if isinstance(current, dict) else None
    if not isinstance(characters, Mapping) or not characters:
        return None
    cards_dir = version_dir / "character_cards"
    files = sorted(cards_dir.glob("*.png")) if cards_dir.is_dir() else ()
    by_character: dict[str, list[Path]] = {}
    for path in files:
        prefix = path.name.split("_", 1)[0]
        by_character.setdefault(prefix, []).append(path)
    outputs = read_version_card_outputs(version_dir)
    entries: list[dict[str, Any]] = []
    for raw_id, data in characters.items():
        character_id = str(raw_id)
        candidates = by_character.get(safe_role_name(character_id)) or ()
        artifact = resolve_character_artifact(candidates, outputs)
        if artifact is None:
            if len(candidates) > 1:
                LOGGER.warning(
                    "stage=card_manifest status=ambiguous_skipped character=%s candidates=%s",
                    character_id,
                    [path.name for path in candidates],
                )
            continue
        entries.append({
            "character_id": character_id,
            "state": "published",
            "artifact_path": _relative_artifact(cards_dir, artifact),
            "artifact_sha256": _artifact_sha(artifact),
            "data_sha256": canonical_character_sha(data if isinstance(data, Mapping) else {}),
            "source_version": str(version),
            "error": None,
        })
    entries.sort(key=lambda entry: _id_sort_key(str(entry["character_id"])))
    return {
        "schema_version": SCHEMA_VERSION,
        "version": str(version),
        "characters": entries,
    }


def read_version_card_outputs(version_dir: Path) -> frozenset[str]:
    """版本根 manifest 中 ``character_cards.outputs`` 的文件名集合（当版明确新产物）。"""
    try:
        payload = json.loads((Path(version_dir) / "manifest.json").read_text(encoding="utf-8"))
    except (OSError, ValueError, TypeError):
        return frozenset()
    section = payload.get("character_cards") if isinstance(payload, dict) else None
    outputs = section.get("outputs") if isinstance(section, dict) else None
    if not isinstance(outputs, list):
        return frozenset()
    return frozenset(Path(str(item)).name for item in outputs)


def resolve_character_artifact(
    candidates: Sequence[Path], outputs: frozenset[str]
) -> Path | None:
    """从同角色候选文件中选出唯一产物。

    单候选直接使用；多候选（改名残留）时恰好一个被版本根 manifest 的
    ``character_cards.outputs`` 声明则取该个；否则返回 None，由调用方
    跳过并记日志，绝不按文件名猜测。
    """
    unique = sorted(set(candidates))
    if len(unique) == 1:
        return unique[0]
    declared = [path for path in unique if path.name in outputs]
    if len(declared) == 1:
        return declared[0]
    return None


def load_effective_manifest(version_dir: Path, version: int | str) -> dict[str, Any] | None:
    """优先读 card manifest，缺失时按旧版实际产物补建（不落盘）。"""
    manifest = load_card_manifest(Path(version_dir) / "character_cards")
    if manifest is not None:
        return manifest
    return legacy_manifest_from_version(version_dir, version)


def copy_manifest_artifacts(
    manifest: Mapping[str, Any],
    cards_dir: Path,
    previous_version_dirs: Sequence[Path],
) -> tuple[str, ...]:
    """把清单引用但 ``cards_dir`` 缺失的长图从旧版本目录补齐（新到旧逐版本找）。

    复制前校验 ``artifact_sha256``，不符继续找更旧版本；找不到就跳过并记日志，
    由查询侧按缺失处理。返回实际复制的文件名。
    """
    cards_dir = Path(cards_dir)
    cards_dir.mkdir(parents=True, exist_ok=True)
    copied: list[str] = []
    entries = manifest.get("characters")
    if not isinstance(entries, list):
        return ()
    for entry in entries:
        if not isinstance(entry, Mapping):
            continue
        artifact_path = str(entry.get("artifact_path") or "")
        sha = str(entry.get("artifact_sha256") or "")
        if not artifact_path:
            continue
        name = Path(artifact_path).name
        target = cards_dir / name
        if target.is_file():
            continue
        for version_dir in previous_version_dirs:
            source = Path(version_dir) / "character_cards" / name
            if not source.is_file():
                continue
            if sha and _artifact_sha(source) != sha:
                continue
            temporary = target.with_name(target.name + ".part")
            shutil.copy2(source, temporary)
            temporary.replace(target)
            copied.append(name)
            break
        else:
            LOGGER.warning(
                "stage=card_manifest status=artifact_missing character=%s artifact=%s",
                entry.get("character_id"),
                name,
            )
    return tuple(copied)


def validate_card_manifest(payload: Mapping[str, Any], cards_dir: Path) -> tuple[str, ...]:
    """校验清单：路径必须相对且不出版本根、文件存在、artifact 哈希与实际字节一致。"""
    cards_dir = Path(cards_dir)
    problems: list[str] = []
    entries = payload.get("characters") if isinstance(payload, Mapping) else None
    if not isinstance(entries, list):
        return ("characters 缺失或不是列表",)
    for entry in entries:
        if not isinstance(entry, Mapping):
            problems.append("条目不是对象")
            continue
        character_id = str(entry.get("character_id"))
        artifact_path = str(entry.get("artifact_path") or "")
        sha = str(entry.get("artifact_sha256") or "")
        if not artifact_path:
            problems.append(f"{character_id}: artifact_path 为空")
            continue
        relative = Path(artifact_path)
        if relative.is_absolute() or ".." in relative.parts:
            problems.append(f"{character_id}: 非法路径 {artifact_path}")
            continue
        artifact = cards_dir.parent / relative
        if not artifact.is_file():
            problems.append(f"{character_id}: 产物缺失 {artifact_path}")
            continue
        if sha and _artifact_sha(artifact) != sha:
            problems.append(f"{character_id}: 产物哈希不符 {artifact_path}")
    return tuple(problems)
