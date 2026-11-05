# -*- coding: utf-8 -*-
"""统一日志配置：`[时间] [级别] 消息`，并注册自定义 SUCCESS 级别。"""

from __future__ import annotations

import logging
from datetime import datetime

from . import config

SUCCESS = 25
logging.addLevelName(SUCCESS, "SUCCESS")

_LOG_FORMAT = "[%(asctime)s] [%(levelname)s] %(message)s"


def _success(self, message: str, *args: object, **kwargs: object) -> None:
    if self.isEnabledFor(SUCCESS):
        self._log(SUCCESS, message, args, **kwargs)


setattr(logging.Logger, "success", _success)


class _Formatter(logging.Formatter):
    """日志时间戳统一按上海时区输出 `YYYY-MM-DD HH:MM:SS`。"""

    def formatTime(self, record: logging.LogRecord, datefmt: str | None = None) -> str:
        return datetime.fromtimestamp(record.created, config.tz()).strftime(
            "%Y-%m-%d %H:%M:%S"
        )


def get_formatter() -> logging.Formatter:
    """统一日志格式；供终端处理器与界面日志处理器复用。"""
    return _Formatter(_LOG_FORMAT)


def setup() -> None:
    """配置根 logger 输出格式；重复调用不会叠加处理器。"""
    root = logging.getLogger()
    root.setLevel(logging.INFO)
    if any(getattr(h, "_monitor_std", False) for h in root.handlers):
        return
    handler = logging.StreamHandler()
    handler.setFormatter(get_formatter())
    setattr(handler, "_monitor_std", True)
    root.addHandler(handler)
