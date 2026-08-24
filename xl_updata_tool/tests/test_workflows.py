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
