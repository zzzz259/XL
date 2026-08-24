import os
import sys

sys.path.insert(0, os.path.dirname(__file__))

from app.platform.crash_reporter import install_crash_reporter
from app.platform.environment import write_environment_report
from app.platform.logger import configure_logging, logger
from app.platform.paths import get_base_dir
from app.platform.runtime_config import parse_runtime_config


def main(argv=None):
    runtime = parse_runtime_config(sys.argv[1:] if argv is None else argv)
    session = configure_logging(runtime)
    crash_reporter = install_crash_reporter(session.directory)
    debug_mode = runtime.debug
    logger.info("application.start mode=%s session=%s", runtime.name, session.session_id)
    if debug_mode:
        try:
            write_environment_report(session.directory)
        except (OSError, PermissionError) as error:
            logger.warning("environment.report_unavailable error=%s", error, exc_info=True)

    try:
        # 运行模式和日志在导入主窗口前就绪，避免业务模块导入时错过 Debug 配置。
        from PySide6.QtWidgets import QApplication
        from PySide6.QtGui import QFont, QFontDatabase
        from app.bootstrap import build_app_context, create_application_runtime
        from app.ui.main_window import MainWindow

        app = QApplication(sys.argv)
        app.setApplicationName("XL Update Tool")
        app.setApplicationVersion("1.0.0")
        font = QFont("Microsoft YaHei UI", 10)
        app.setFont(font)
        app_context = build_app_context(runtime)
        app_runtime = create_application_runtime(app_context)
        try:
            base = get_base_dir()
            font_path = os.path.join(base, "app", "resources", "font.ttf")
            if os.path.exists(font_path):
                QFontDatabase.addApplicationFont(font_path)
        except Exception as e:
            logger.warning("font.load_failed error=%s", e, exc_info=True)
        window = MainWindow(debug_mode=debug_mode, runtime=app_runtime)
        window.show()
        sys.exit(app.exec())
    except Exception as e:
        crash_reporter.write(*sys.exc_info(), source="startup")
        logger.error("application.start_failed error=%s", e, exc_info=True)
        sys.exit(1)


if __name__ == "__main__":
    main()
