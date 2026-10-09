"""Book Collection Checker - what you have and what's missing in your audiobook / ebook series."""
import sys

from PySide6.QtGui import QIcon
from PySide6.QtWidgets import QApplication

from app.config import load_settings
from app.portable import app_dir
from app.ui import theme
from app.ui.main_window import MainWindow


def main() -> int:
    app = QApplication(sys.argv)
    app.setApplicationName("Book Collection Checker")
    app.setStyle("Fusion")
    base = getattr(sys, "_MEIPASS", None) or str(app_dir())
    app.setWindowIcon(QIcon(f"{base}/assets/icon.png"))
    theme.apply(load_settings().theme, app)
    w = MainWindow()
    w.show()
    return app.exec()


if __name__ == "__main__":
    sys.exit(main())
