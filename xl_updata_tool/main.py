import sys
import os

sys.path.insert(0, os.path.dirname(__file__))

from PySide6.QtWidgets import QApplication
from PySide6.QtGui import QFont, QFontDatabase
from app.ui.main_window import MainWindow
from app.core.logger import logger
from app.core.path_utils import get_base_dir


def main():
    debug_mode = '--debug' in sys.argv
    if debug_mode:
        logger.info("========== XL Update Tool 启动（DEBUG 模式） ==========")
    else:
        logger.info("========== XL Update Tool 启动（正常模式） ==========")
    try:
        app = QApplication(sys.argv)
        app.setApplicationName("XL Update Tool")
        app.setApplicationVersion("1.0.0")
        font = QFont("Microsoft YaHei UI", 10)
        app.setFont(font)
        try:
            base = get_base_dir()
            font_path = os.path.join(base, "app", "resources", "font.ttf")
            if os.path.exists(font_path):
                QFontDatabase.addApplicationFont(font_path)
        except Exception as e:
            logger.warning(f"字体加载失败: {e}")
        window = MainWindow(debug_mode=debug_mode)
        window.show()
        sys.exit(app.exec())
    except Exception as e:
        logger.error(f"应用启动失败: {e}", exc_info=True)
        sys.exit(1)


if __name__ == "__main__":
    main()
