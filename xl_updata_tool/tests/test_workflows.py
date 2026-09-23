from app.bootstrap.workflows import ImportPostprocessWorkflow
from app.shared.contracts import ImportResult


class FakeSignal:
    def __init__(self):
        self.handlers = []

    def connect(self, handler):
        self.handlers.append(handler)

    def emit(self, *args):
        for handler in tuple(self.handlers):
            handler(*args)


class FakeImporter:
    def __init__(self, result):
        self.result_ready = FakeSignal()
        self.last_result = result


class FakeAudio:
    def __init__(self):
        self.processing_finished = FakeSignal()
        self.processing_cancelled = FakeSignal()
        self.processing_error = FakeSignal()
        self.started = []

    def start_decrypt(self, **kwargs):
        self.started.append(kwargs)


class FakePreview:
    def __init__(self):
        self.processing_finished = FakeSignal()
        self.processing_cancelled = FakeSignal()
        self.processing_error = FakeSignal()
        self.started = []

    def start_postprocess(self, **kwargs):
        self.started.append(kwargs)


class FakeCharacters:
    def __init__(self):
        self.calls = []

    def auto_parse_after_lua_export(self, result, progress_dialog=None):
        self.calls.append((result, progress_dialog))


class FakeRegistry:
    def pending(self, result):
        return result.postprocess_categories if result else frozenset()


def test_import_postprocess_workflow_routes_audio_then_lua():
    result = ImportResult(
        categories=frozenset({"lua", "audio"}),
        completed_categories=frozenset({"lua", "audio"}),
        postprocess_categories=frozenset({"lua", "audio"}),
        lua_export_result={"directory": "output/lua/20260824"},
    )
    importer = FakeImporter(result)
    audio = FakeAudio()
    characters = FakeCharacters()
    workflow = ImportPostprocessWorkflow(importer, audio, characters, FakeRegistry())
    finished = []
    importer.result_ready.emit(result)

    workflow.handle_import_finished(True, "导入完成", "dialog", lambda *args, **kwargs: finished.append((args, kwargs)))
    assert audio.started == [{"force": False, "shared_dialog": "dialog"}]
    assert finished == []

    audio.processing_finished.emit(True)

    assert characters.calls == [({"directory": "output/lua/20260824"}, "dialog")]
    assert finished == [( (True, "导入完成"), {"audio_error": None})]


def test_import_postprocess_workflow_routes_audio_then_preview_then_lua():
    result = ImportResult(
        categories=frozenset({"lua", "character", "audio"}),
        completed_categories=frozenset({"lua", "character", "audio"}),
        postprocess_categories=frozenset({"lua", "audio", "preview"}),
    )
    importer = FakeImporter(result)
    audio = FakeAudio()
    preview = FakePreview()
    characters = FakeCharacters()
    workflow = ImportPostprocessWorkflow(importer, audio, characters, FakeRegistry(), preview=preview)
    finished = []
    importer.result_ready.emit(result)

    workflow.handle_import_finished(True, "导入完成", "dialog", lambda *args, **kwargs: finished.append((args, kwargs)))
    audio.processing_finished.emit(True)

    assert preview.started == [{"force": False, "shared_dialog": "dialog"}]
    assert finished == []

    preview.processing_finished.emit(True)

    assert characters.calls == [(None, "dialog")]
    assert finished == [((True, "导入完成"), {"audio_error": None})]


def test_import_postprocess_workflow_does_not_skip_required_preview_processing():
    result = ImportResult(
        categories=frozenset({"character", "fgui"}),
        completed_categories=frozenset({"character", "fgui"}),
    )
    importer = FakeImporter(result)
    preview = FakePreview()
    finished = []
    workflow = ImportPostprocessWorkflow(
        importer,
        FakeAudio(),
        FakeCharacters(),
        FakeRegistry(),
        preview=preview,
    )
    importer.result_ready.emit(result)

    workflow.handle_import_finished(
        True,
        "导入完成",
        "dialog",
        lambda *args, **kwargs: finished.append((args, kwargs)),
    )

    assert preview.started == []
    assert finished and finished[0][0][0] is False
    assert "图片资源后处理未启动" in finished[0][0][1]
