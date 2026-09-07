"""Qt application bootstrap."""
from __future__ import annotations

import logging
import sys

from themover.config import app_data_dir, load_settings


def run_app(argv: list[str]) -> int:
    logging.basicConfig(
        level=logging.DEBUG if "--debug" in argv else logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
        handlers=[logging.StreamHandler(), logging.FileHandler(app_data_dir() / "themover.log", encoding="utf-8")],
    )
    from PySide6.QtWidgets import QApplication

    from themover.ui.main_window import MainWindow
    from themover.ui.style import STYLESHEET

    app = QApplication.instance() or QApplication(sys.argv[:1])
    app.setApplicationName("The Mover")
    app.setStyleSheet(STYLESHEET)
    icon = _icon_path()
    if icon:
        from PySide6.QtGui import QIcon

        app.setWindowIcon(QIcon(icon))
    settings = load_settings()
    window = MainWindow(settings)
    if settings.start_minimized:
        window.showMinimized()
    else:
        window.show()
    return app.exec()


def _icon_path() -> str:
    """Locate assets/icon.png both from source and inside a PyInstaller bundle."""
    import os

    candidates = [
        os.path.join(getattr(sys, "_MEIPASS", ""), "assets", "icon.png"),
        os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))), "assets", "icon.png"),
    ]
    for c in candidates:
        if c and os.path.exists(c):
            return c
    return ""
