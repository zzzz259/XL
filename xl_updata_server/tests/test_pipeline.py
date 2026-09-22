from server_app.pipeline import ProcessResult, UpdatePipeline
from server_app.versioning import read_current_pointer


def test_pipeline_does_not_replace_current_output_when_processing_fails(tmp_path):
    current = tmp_path / "character_data" / "current.json"
    current.parent.mkdir()
    current.write_text('{"version": 1}', encoding="utf-8")
    def failing_processor(_staging_dir):
        raise RuntimeError("extractor failed")

    pipeline = UpdatePipeline(tmp_path, processor=failing_processor)
    result = pipeline.run_once()
    assert result.processed is False
    assert current.read_text(encoding="utf-8") == '{"version": 1}'


def test_pipeline_converts_unexpected_runtime_failure_to_result(tmp_path):
    def failing_processor(_staging_dir):
        raise ModuleNotFoundError("optional runtime dependency")

    result = UpdatePipeline(tmp_path, processor=failing_processor).run_once()

    assert result.processed is False
    assert result.error == "optional runtime dependency"


def test_pipeline_publishes_successful_result_into_version_directory(tmp_path):
    def successful_processor(staging_dir):
        (staging_dir / "manifest.json").write_text('{"version": 123}', encoding="utf-8")
        return ProcessResult(version_timestamp=123, processed=True)

    result = UpdatePipeline(tmp_path, processor=successful_processor).run_once()

    assert result.processed is True
    assert (tmp_path / "versions" / "123" / "manifest.json").is_file()
    assert read_current_pointer(tmp_path) == 123


def test_process_result_exposes_skin_and_card_counts():
    result = ProcessResult(skin_count=3, card_count=2, card_warning_count=1, card_failure_count=0)

    assert result.skin_count == 3
    assert result.card_count == 2
    assert result.card_warning_count == 1
    assert result.card_failure_count == 0


def test_pipeline_mirrors_character_data_to_data_root(tmp_path):
    def successful_processor(staging_dir):
        current = staging_dir / "character_data" / "current.json"
        current.parent.mkdir(parents=True, exist_ok=True)
        current.write_text('{"version": 123, "characters": {"1": {}}}', encoding="utf-8")
        return ProcessResult(version_timestamp=123, processed=True)

    result = UpdatePipeline(tmp_path, processor=successful_processor).run_once()

    assert result.processed is True
    mirror = tmp_path / "character_data" / "current.json"
    assert mirror.is_file()
    assert '"characters"' in mirror.read_text(encoding="utf-8")
