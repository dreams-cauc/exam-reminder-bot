# -*- coding: utf-8 -*-
"""应用入口：构建 QApplication、加载样式、装配主窗口。"""

from __future__ import annotations

import sys

from PyQt5.QtCore import QFile, QIODevice, Qt
from PyQt5.QtWidgets import QApplication

# 导入即注册 qrc 编译进来的 Qt 资源（样式文件被打包进资源，便于后续打包分发）
from ..style import resources_rc  # noqa: F401
from ..utils import log
from ..utils.settings import settings
from .main_window import MainWindow

_QSS_RESOURCE = ":/style/light.qss"


def _load_qss() -> str:
    """从 Qt 资源系统读取样式，不依赖运行时的外部文件路径。"""
    qss = QFile(_QSS_RESOURCE)
    if not qss.open(QIODevice.ReadOnly | QIODevice.Text):
        return ""
    data = bytes(qss.readAll()).decode("utf-8")
    qss.close()
    return data


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
