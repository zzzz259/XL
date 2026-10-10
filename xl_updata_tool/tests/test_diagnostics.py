import sys
import json
import subprocess
from pathlib import Path
from types import SimpleNamespace

from PySide6.QtCore import QtMsgType, qInstallMessageHandler, qWarning

from app.platform.crash_reporter import CrashReporter
from app.platform.logger import configure_logging, logger
from app.platform.processes import run_external_process
from app.platform.runtime_config import RuntimeConfig, parse_runtime_config
from app.platform.task_context import set_task_outcome, stage_context, task_context


def _flush_logger():
    for handler in logger.handlers:
        handler.flush()


def test_parse_runtime_config_keeps_unknown_qt_arguments():
    config = parse_runtime_config(["--debug", "--platform", "offscreen"])

    assert config.debug is True
    assert config.name == "DEBUG"
    assert config.extra_args == ("--platform", "offscreen")

    normal = RuntimeConfig()
    assert normal.log_level <= 10
    assert normal.capture_external_output is True


def test_parse_runtime_config_supports_self_check():
    config = parse_runtime_config(["--self-check"])

    assert config.self_check is True
    assert config.debug is False

    default = parse_runtime_config([])
    assert default.self_check is False

    # --self-check 可与 --debug 组合，未知参数仍原样保留。
    combined = parse_runtime_config(["--self-check", "--debug", "--platform", "offscreen"])
    assert combined.self_check is True
    assert combined.debug is True
    assert combined.extra_args == ("--platform", "offscreen")


def test_logging_profiles_and_task_context(tmp_path):
    normal = configure_logging(RuntimeConfig(debug=False), logs_dir=tmp_path / "normal")
    logger.debug("normal.debug.should_be_hidden")
    logger.info("normal.info")
    _flush_logger()

    normal_text = normal.app_log.read_text(encoding="utf-8")
    assert "normal.info" in normal_text
    assert "normal.debug.should_be_hidden" not in normal_text
    assert normal.debug_log is None
    event_lines = [json.loads(line) for line in normal.events_log.read_text(encoding="utf-8").splitlines()]
    assert any(event["message"] == "normal.debug.should_be_hidden" for event in event_lines)

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


def test_process_runner_logs_exit_and_output_tail(tmp_path):
    configure_logging(RuntimeConfig(debug=True), logs_dir=tmp_path / "logs")

    result = run_external_process(
        [sys.executable, "-c", "import sys; print('stdout text'); print('stderr text', file=sys.stderr); sys.exit(3)"],
        tool="fake-tool",
        capture_output=True,
        text=True,
    )
    _flush_logger()

    assert result.returncode == 3
    text = next((tmp_path / "logs").glob("*/debug.log")).read_text(encoding="utf-8")
    assert "process.start command_id=" in text
    assert "process.failed command_id=" in text
    assert "tool=fake-tool exit_code=3" in text
    assert "stderr text" in text


def test_process_runner_persists_successful_stdout_and_stderr_in_normal_mode(tmp_path):
    session = configure_logging(RuntimeConfig(debug=False), logs_dir=tmp_path / "logs")

    result = run_external_process(
        [sys.executable, "-c", "import sys; print('tool stdout'); print('tool stderr', file=sys.stderr)"],
        tool="python-test",
        capture_output=True,
        text=True,
    )
    _flush_logger()

    assert result.returncode == 0
    assert result.stdout.strip() == "tool stdout"
    assert "tool stderr" in result.stderr
    external_dir = session.directory / "external"
    stdout_log = next(external_dir.rglob("stdout.log"))
    stderr_log = next(external_dir.rglob("stderr.log"))
    assert "tool stdout" in stdout_log.read_text(encoding="utf-8")
    assert "tool stderr" in stderr_log.read_text(encoding="utf-8")
    assert "process.exit" in session.app_log.read_text(encoding="utf-8")
    command_manifest = json.loads(next(external_dir.rglob("command.json")).read_text(encoding="utf-8"))
    assert command_manifest["session_id"] == session.session_id
    assert command_manifest["task_id"] == "-"
    assert command_manifest["command_id"]


def test_process_runner_caps_persisted_output_and_keeps_tail(tmp_path):
    session = configure_logging(RuntimeConfig(debug=False), logs_dir=tmp_path / "logs")
    command = [
        sys.executable,
        "-c",
        "import sys; sys.stdout.write('x' * 10000 + 'FINAL_MARKER')",
    ]

    result = run_external_process(
        command,
        tool="large-output",
        capture_output=True,
        text=True,
        max_output_bytes=512,
    )

    assert result.returncode == 0
    assert result.stdout.endswith("FINAL_MARKER")
    output_log = next((session.directory / "external").rglob("stdout.log"))
    persisted = output_log.read_text(encoding="utf-8")
    assert "OUTPUT TRUNCATED" in persisted
    assert persisted.endswith("FINAL_MARKER")
    assert output_log.stat().st_size < 700


def test_process_runner_drains_both_streams_and_dispatches_lines(tmp_path):
    session = configure_logging(RuntimeConfig(debug=False), logs_dir=tmp_path / "logs")
    observed = []
    script = (
        "import sys; "
        "[(print(f'out-{i}'), print(f'err-{i}', file=sys.stderr)) for i in range(1500)]; "
        "print('stdout-final'); print('stderr-final', file=sys.stderr)"
    )

    result = run_external_process(
        [sys.executable, "-c", script],
        tool="two-stream-test",
        capture_output=True,
        text=True,
        on_line=lambda stream, line: observed.append((stream, line)),
    )

    assert result.returncode == 0
    assert "stdout-final" in result.stdout
    assert "stderr-final" in result.stderr
    assert ("stdout", "stdout-final") in observed
    assert ("stderr", "stderr-final") in observed
    output_dir = session.directory / "external"
    assert "out-1499" in next(output_dir.rglob("stdout.log")).read_text(encoding="utf-8")
    assert "err-1499" in next(output_dir.rglob("stderr.log")).read_text(encoding="utf-8")


def test_process_runner_copies_tool_generated_logs_into_session(tmp_path):
    session = configure_logging(RuntimeConfig(debug=False), logs_dir=tmp_path / "logs")
    native_log = tmp_path / "tool-native.log"
    native_log.write_text("native tool diagnostic", encoding="utf-8")

    run_external_process(
        [sys.executable, "-c", "pass"],
        tool="python-test",
        capture_output=True,
        text=True,
        tool_log_paths=[native_log],
    )
    _flush_logger()

    collected = list((session.directory / "external").rglob("*tool-native.log"))
    assert len(collected) == 1
    assert collected[0].read_text(encoding="utf-8") == "native tool diagnostic"


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


def test_crash_reporter_writes_unraisable_exception(tmp_path):
    reporter = CrashReporter(tmp_path)
    try:
        raise RuntimeError("unraisable diagnostic failure")
    except RuntimeError as error:
        args = SimpleNamespace(
            object=object(),
            exc_type=type(error),
            exc_value=error,
            exc_traceback=error.__traceback__,
        )
        report = reporter.handle_unraisable(args)

    assert "source: unraisable:object" in report.read_text(encoding="utf-8")
    assert "RuntimeError: unraisable diagnostic failure" in report.read_text(encoding="utf-8")


def test_crash_reporter_persists_qt_messages(tmp_path):
    session = configure_logging(RuntimeConfig(debug=False), logs_dir=tmp_path / "logs")
    reporter = CrashReporter(session.directory)
    previous_handler = reporter.install_qt_handler()
    try:
        qWarning("synthetic Qt warning for crash diagnostics")
    finally:
        qInstallMessageHandler(previous_handler)
    _flush_logger()

    assert "synthetic Qt warning for crash diagnostics" in session.error_log.read_text(encoding="utf-8")


def test_crash_reporter_writes_report_for_qt_fatal_message(tmp_path):
    session = configure_logging(RuntimeConfig(debug=False), logs_dir=tmp_path / "logs")
    reporter = CrashReporter(session.directory)

    reporter.handle_qt_message(
        QtMsgType.QtFatalMsg,
        SimpleNamespace(category="qt.test", file="sample.cpp", line=42, function="run"),
        "synthetic fatal Qt message",
    )
    _flush_logger()

    report = next(session.directory.glob("crash_*.log"))
    assert "source: Qt fatal message" in report.read_text(encoding="utf-8")
    assert "synthetic fatal Qt message" in report.read_text(encoding="utf-8")
    events = [json.loads(line) for line in session.events_log.read_text(encoding="utf-8").splitlines()]
    assert any(event.get("error_code") == "QT_FATAL" for event in events)


def test_qt_fatal_process_leaves_diagnostics_before_exit(tmp_path):
    log_root = tmp_path / "fatal-child-logs"
    script = """
import ctypes
import faulthandler
import sys
ctypes.windll.kernel32.SetErrorMode(0x0001 | 0x0002 | 0x8000)
from app.platform.crash_reporter import install_crash_reporter
from app.platform.logger import configure_logging
session = configure_logging(logs_dir=sys.argv[1])
reporter = install_crash_reporter(session.directory)
assert faulthandler.is_enabled()
reporter.install_qt_handler()
from PySide6.QtCore import qFatal
qFatal('controlled fatal for regression test')
"""
    result = subprocess.run(
        [sys.executable, "-c", script, str(log_root)],
        cwd=Path(__file__).parents[1],
        capture_output=True,
        text=True,
        timeout=15,
        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
    )

    sessions = list(log_root.iterdir())
    assert result.returncode != 0
    assert len(sessions) == 1
    assert "qt.message type=fatal" in (sessions[0] / "app.log").read_text(encoding="utf-8")
    assert "qt.message type=fatal" in (sessions[0] / "error.log").read_text(encoding="utf-8")
    assert "source: Qt fatal message" in next(sessions[0].glob("crash_*.log")).read_text(encoding="utf-8")
    assert (sessions[0] / "fatal.log").exists()


def test_crash_reporter_marks_previous_session_without_clean_exit(tmp_path):
    session = configure_logging(RuntimeConfig(debug=False), logs_dir=tmp_path / "logs")
    previous = session.directory.parent / "20261008_120000_000000"
    previous.mkdir()
    (previous / "app.log").write_text(
        "2026-10-08 INFO application.start mode=NORMAL\n", encoding="utf-8"
    )
    reporter = CrashReporter(session.directory)

    reporter.report_previous_unclean_exit(session.directory)
    _flush_logger()

    report = previous / "crash_detected.log"
    assert report.exists()
    assert "application.shutdown" in report.read_text(encoding="utf-8")
    assert "application.previous_session_unclean_exit" in session.app_log.read_text(encoding="utf-8")


def test_task_context_writes_explicit_outcome_summary(tmp_path):
    session = configure_logging(RuntimeConfig(debug=False), logs_dir=tmp_path / "logs")
    with task_context("IMPORT", task_id="IMPORT-TEST", component="import"):
        set_task_outcome(
            "failed",
            error_code="AS_NO_EXPECTED_OUTPUT",
            message="AssetStudio exited without artifacts",
            details={"exit_code": 0, "output_verified": False},
        )

    summary_path = session.directory / "tasks" / "IMPORT-TEST" / "summary.json"
    summary = json.loads(summary_path.read_text(encoding="utf-8"))
    assert summary["task_id"] == "IMPORT-TEST"
    assert summary["outcome"] == "failed"
    assert summary["error_code"] == "AS_NO_EXPECTED_OUTPUT"
    assert summary["details"] == {"exit_code": 0, "output_verified": False}


def test_task_detail_log_is_isolated_from_session_summary_log(tmp_path):
    session = configure_logging(RuntimeConfig(debug=False), logs_dir=tmp_path / "logs")
    with task_context("BUNDLE_DOWNLOAD", task_id="BUNDLE_DOWNLOAD-ISOLATED", component="versions"):
        logger.info("bundle.task.detail", extra={"event": "bundle.task.detail"})
        logger.warning("bundle.task.warning", extra={"event": "bundle.task.warning"})

    task_log = session.directory / "tasks" / "BUNDLE_DOWNLOAD-ISOLATED" / "task.log"
    task_text = task_log.read_text(encoding="utf-8")
    app_text = session.app_log.read_text(encoding="utf-8")
    assert "bundle.task.detail" in task_text
    assert "bundle.task.warning" in task_text
    assert "task.start" in app_text
    assert "task.finish" in app_text
    assert "bundle.task.detail" not in app_text
    assert "bundle.task.warning" not in app_text
    assert "bundle.task.warning" not in session.error_log.read_text(encoding="utf-8")
    task_events = [
        json.loads(line)
        for line in (task_log.parent / "events.jsonl").read_text(encoding="utf-8").splitlines()
    ]
    summary_events = [
        json.loads(line)
        for line in session.events_log.read_text(encoding="utf-8").splitlines()
    ]
    assert any(event["event"] == "bundle.task.detail" for event in task_events)
    assert any(event["event"] == "task.start" for event in summary_events)
    assert not any(event["event"] == "bundle.task.detail" for event in summary_events)
    assert not any(event["event"] == "bundle.task.warning" for event in summary_events)


def test_external_process_logs_are_nested_under_active_task(tmp_path):
    session = configure_logging(RuntimeConfig(debug=False), logs_dir=tmp_path / "logs")
    with task_context("IMPORT", task_id="IMPORT-EXTERNAL", component="import"):
        run_external_process(
            [sys.executable, "-c", "print('import tool output')"],
            tool="python-test",
            capture_output=True,
            text=True,
        )

    task_external = session.directory / "tasks" / "IMPORT-EXTERNAL" / "external"
    assert "import tool output" in next(task_external.rglob("stdout.log")).read_text(encoding="utf-8")


def test_external_output_callback_inherits_task_log_context(tmp_path):
    session = configure_logging(RuntimeConfig(debug=False), logs_dir=tmp_path / "logs")
    with task_context("IMPORT", task_id="IMPORT-CALLBACK", component="import"):
        run_external_process(
            [sys.executable, "-c", "print('callback marker')"],
            tool="python-test",
            capture_output=True,
            text=True,
            on_line=lambda _stream, line: logger.info(
                "external.callback.line %s", line, extra={"event": "external.callback.line"}
            ),
        )

    task_dir = session.directory / "tasks" / "IMPORT-CALLBACK"
    assert "callback marker" in (task_dir / "task.log").read_text(encoding="utf-8")
    assert "external.callback.line" not in session.app_log.read_text(encoding="utf-8")


def test_task_return_without_business_result_is_not_reported_as_success(tmp_path):
    session = configure_logging(RuntimeConfig(debug=False), logs_dir=tmp_path / "logs")
    with task_context("UNKNOWN", task_id="UNKNOWN-TEST"):
        pass

    summary = json.loads(
        (session.directory / "tasks" / "UNKNOWN-TEST" / "summary.json").read_text(encoding="utf-8")
    )
    assert summary["outcome"] == "unknown"
