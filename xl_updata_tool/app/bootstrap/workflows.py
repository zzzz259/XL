"""Composition Root 负责的跨 Feature 导入后处理工作流。"""

from __future__ import annotations

from collections.abc import Callable

from app.shared.contracts import ImportResult


class ImportPostprocessWorkflow:
    """把 ImportResult 显式路由到 Audio/Characters 后处理器。

    Shell 只提交导入完成事件和 UI 进度端口，不再判断 postprocess 分类或
    直接连接 Audio/Characters 的处理完成信号。
    """

    def __init__(self, importer, audio, characters, registry):
        self.importer = importer
        self.audio = audio
        self.characters = characters
        self.registry = registry
        self._latest_result: ImportResult | None = None
        self._message = ""
        self._progress_dialog = None
        self._finish: Callable[..., None] | None = None
        self.importer.result_ready.connect(self._remember_result)
        self.audio.processing_finished.connect(self._on_audio_finished)
        self.audio.processing_cancelled.connect(self._on_audio_cancelled)
        self.audio.processing_error.connect(self._on_audio_error)

    def _remember_result(self, result: ImportResult) -> None:
        self._latest_result = result

    def handle_import_finished(self, success, message, progress_dialog, finish) -> None:
        self._message = message
        self._progress_dialog = progress_dialog
        self._finish = finish
        if not success:
            finish(False, message)
            return
        result = self._latest_result or self.importer.last_result
        if "audio" in self.registry.pending(result):
            self.audio.start_decrypt(force=False, shared_dialog=progress_dialog)
            return
        self._finish_success(result)

    def _on_audio_finished(self, shared) -> None:
        if shared and self._finish is not None:
            self._finish_success(self._latest_result or self.importer.last_result)

    def _on_audio_cancelled(self, shared) -> None:
        if shared and self._finish is not None:
            self._finish(
                False,
                "音频后处理已取消，已完成的文件已保留，可稍后重新处理音频。",
                cancelled=True,
            )
            self._clear()

    def _on_audio_error(self, error_message, shared) -> None:
        if shared and self._finish is not None:
            self._finish_success(
                self._latest_result or self.importer.last_result,
                audio_error=error_message,
            )

    def _finish_success(self, result, audio_error=None) -> None:
        if self._finish is None:
            return
        lua_result = result.lua_export_result if result else None
        if result is None or "lua" in self.registry.pending(result):
            self.characters.auto_parse_after_lua_export(
                lua_result,
                progress_dialog=self._progress_dialog,
            )
        self._finish(True, self._message, audio_error=audio_error)
        self._clear()

    def _clear(self) -> None:
        self._message = ""
        self._progress_dialog = None
        self._finish = None
