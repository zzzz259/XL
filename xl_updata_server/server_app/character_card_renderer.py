"""Node 渲染器桥接：把角色记录批量提交给 Node 长图渲染器。"""

from __future__ import annotations

import json
import logging
import subprocess
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .character_cards import CharacterCardRecord

LOGGER = logging.getLogger(__name__)

DEFAULT_NODE_BIN = "node"
DEFAULT_TIMEOUT_SECONDS = 300


@dataclass(frozen=True)
class CharacterCardRenderResult:
    """单角色渲染结果。"""

    character_id: str
    output_path: Path
    height: int | None
    warnings: tuple[str, ...]
    error: str | None


def _renderer_directory(renderer_dir: str | Path | None) -> Path:
    if renderer_dir:
        return Path(renderer_dir).expanduser().resolve()
    return (Path(__file__).resolve().parent.parent / "renderer").resolve()


def render_character_cards_batch(
    records: tuple[CharacterCardRecord, ...],
    out_dir: str | Path,
    node_bin: str = DEFAULT_NODE_BIN,
    renderer_dir: str | Path | None = None,
    name_template: str = "{id}_{name}_角色档案_长图.png",
) -> tuple[CharacterCardRenderResult, ...]:
    """批量渲染角色档案长图，通过一次 Node 进程完成。

    找不到 node 或渲染器脚本时抛出 FileNotFoundError。
    单个角色渲染失败不会中断批次，失败信息会记录在结果的 error 字段中。
    """

    renderer = _renderer_directory(renderer_dir)
    render_batch_js = renderer / "render_batch.js"
    if not render_batch_js.is_file():
        raise FileNotFoundError(f"渲染器脚本不存在: {render_batch_js}")

    output_dir = Path(out_dir).expanduser().resolve()
    output_dir.mkdir(parents=True, exist_ok=True)

    payload = {
        "characters": {record.character_id: record.data for record in records},
        "ids": [record.character_id for record in records],
        "out_dir": str(output_dir),
        "name_template": name_template,
    }

    with tempfile.NamedTemporaryFile(
        mode="w", suffix=".json", encoding="utf-8", delete=False
    ) as handle:
        json.dump(payload, handle, ensure_ascii=False, separators=(",", ":"))
        payload_path = Path(handle.name)

    try:
        command = [
            node_bin,
            "--max-old-space-size=512",
            str(render_batch_js),
            str(payload_path),
        ]
        try:
            completed = subprocess.run(
                command,
                cwd=renderer,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=DEFAULT_TIMEOUT_SECONDS,
            )
        except FileNotFoundError as error:
            raise FileNotFoundError(f"找不到 node 可执行文件: {node_bin}") from error

        stdout = completed.stdout or "{}"
        try:
            report = json.loads(stdout)
        except json.JSONDecodeError as error:
            raise RuntimeError(
                f"渲染器输出无法解析为 JSON (exit={completed.returncode}):\n"
                f"stdout={stdout[:500]}\nstderr={completed.stderr[:500]}"
            ) from error

        results = report.get("results", [])
        by_id: dict[str, dict[str, Any]] = {item["id"]: item for item in results}

        render_results: list[CharacterCardRenderResult] = []
        for record in records:
            item = by_id.get(record.character_id, {})
            if item.get("ok"):
                render_results.append(
                    CharacterCardRenderResult(
                        character_id=record.character_id,
                        output_path=Path(item["path"]),
                        height=int(item["height"]) if "height" in item else None,
                        warnings=tuple(item.get("warnings", [])),
                        error=None,
                    )
                )
            else:
                render_results.append(
                    CharacterCardRenderResult(
                        character_id=record.character_id,
                        output_path=output_dir / name_template.replace("{id}", record.character_id).replace("{name}", "未知"),
                        height=None,
                        warnings=(),
                        error=str(item.get("error", "未知错误")),
                    )
                )
                LOGGER.warning(
                    "角色 %s 长图渲染失败: %s",
                    record.character_id,
                    item.get("error", "未知错误"),
                )
        return tuple(render_results)
    finally:
        try:
            payload_path.unlink(missing_ok=True)
        except OSError:
            pass


def render_character_card(
    record: CharacterCardRecord,
    output_path: str | Path,
    font_path: str | Path | None = None,
    node_bin: str = DEFAULT_NODE_BIN,
    renderer_dir: str | Path | None = None,
) -> tuple[int, tuple[str, ...]]:
    """保持旧版单文件渲染契约，内部转调批量渲染器。

    font_path 参数已弃用：字体资源现在由 renderer/assets/fonts/ 管理。
    """

    output_path = Path(output_path).expanduser().resolve()
    results = render_character_cards_batch(
        (record,),
        out_dir=output_path.parent,
        node_bin=node_bin,
        renderer_dir=renderer_dir,
        name_template=output_path.name,
    )
    result = results[0]
    if result.error:
        raise RuntimeError(result.error)
    if result.height is None:
        raise RuntimeError("渲染器未返回图片高度")
    return result.height, result.warnings
