# -*- coding: utf-8 -*-
"""应用入口：构建 QApplication、加载样式、装配主窗口。"""

from __future__ import annotations

import sys

from PyQt5.QtCore import Qt
from PyQt5.QtWidgets import QApplication

from ..utils import config, log
from ..utils.settings import settings
from .main_window import MainWindow


def _load_qss() -> str:
    path = config.PROJECT_ROOT / "src" / "style" / "light.qss"
    return path.read_text(encoding="utf-8") if path.exists() else ""


def run() -> int:
    QApplication.setAttribute(Qt.AA_EnableHighDpiScaling, True)
    QApplication.setAttribute(Qt.AA_UseHighDpiPixmaps, True)
    app = QApplication(sys.argv)
    app.setStyleSheet(_load_qss())

    log.setup()
    settings.ensure_initialized()

    window = MainWindow()
    window.show()
    return app.exec_()
