import subprocess

from app.core.crash_reporter import CrashReporter
from app.core.logger import configure_logging, logger
from app.core.process_runner import run_external_process
from app.core.runtime_config import RuntimeConfig, parse_runtime_config
from app.core.task_context import stage_context, task_context


def _flush_logger():
    for handler in logger.handlers:
        handler.flush()


def test_parse_runtime_config_keeps_unknown_qt_arguments():
    config = parse_runtime_config(["--debug", "--platform", "offscreen"])

    assert config.debug is True
    assert config.name == "DEBUG"
    assert config.extra_args == ("--platform", "offscreen")


def test_logging_profiles_and_task_context(tmp_path):
    normal = configure_logging(RuntimeConfig(debug=False), logs_dir=tmp_path / "normal")
    logger.debug("normal.debug.should_be_hidden")
    logger.info("normal.info")
    _flush_logger()

    normal_text = normal.app_log.read_text(encoding="utf-8")
    assert "normal.info" in normal_text
    assert "normal.debug.should_be_hidden" not in normal_text
    assert normal.debug_log is None

    debug = configure_logging(RuntimeConfig(debug=True), logs_dir=tmp_path / "debug")
    with task_context("AUDIO", task_id="AUDIO-TEST", component="audio"):
        with stage_context("audio.bank", "decode"):
            logger.debug("bank.complete outputs=2")
    _flush_logger()

    debug_text = debug.debug_log.read_text(encoding="utf-8")
    assert "task=AUDIO-TEST" in debug_text
    assert "[audio.bank]" in debug_text
    assert "[stage=decode]" in debug_text
    assert "bank.complete outputs=2" in debug_text


def test_process_runner_logs_exit_and_output_tail(tmp_path, monkeypatch):
    configure_logging(RuntimeConfig(debug=True), logs_dir=tmp_path / "logs")
    completed = subprocess.CompletedProcess(
        args=["fake-tool"], returncode=3, stdout="stdout text", stderr="stderr text"
    )
    monkeypatch.setattr(subprocess, "run", lambda *args, **kwargs: completed)

    result = run_external_process(
        ["fake-tool", "--input", "sample"],
        tool="fake-tool",
        capture_output=True,
        text=True,
    )
    _flush_logger()

    assert result.returncode == 3
    text = next((tmp_path / "logs").glob("*/debug.log")).read_text(encoding="utf-8")
    assert "process.start tool=fake-tool" in text
    assert "process.failed tool=fake-tool exit_code=3" in text
    assert "stderr text" in text


def test_crash_reporter_writes_traceback(tmp_path):
    reporter = CrashReporter(tmp_path)
    try:
        raise ValueError("diagnostic failure")
    except ValueError as error:
        report = reporter.write(type(error), error, error.__traceback__, source="test")

    assert report.exists()
    text = report.read_text(encoding="utf-8")
    assert "source: test" in text
    assert "ValueError: diagnostic failure" in text
