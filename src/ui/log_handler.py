# -*- coding: utf-8 -*-
"""把标准库日志桥接到界面日志区（跨线程安全）。"""

from __future__ import annotations

import logging

from PyQt5.QtCore import QObject, pyqtSignal

from ..utils import log


class LogBridge(QObject):
    """承载日志信号；在 GUI 线程创建，后台线程 emit 时自动排队到 GUI 线程。"""

    message = pyqtSignal(str)


class QtLogHandler(logging.Handler):
    """把日志记录转发给界面日志框，复用统一的 `[时间] [级别] 消息` 格式。"""

    def __init__(self, bridge: LogBridge) -> None:
        super().__init__()
        self._bridge = bridge
        self.setFormatter(log.get_formatter())

    def emit(self, record: logging.LogRecord) -> None:
        try:
            text = self.format(record)
        except Exception:  # 格式化失败也不能打断业务线程
            text = record.getMessage()
        self._bridge.message.emit(text)
