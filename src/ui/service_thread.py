# -*- coding: utf-8 -*-
"""后台提醒服务线程：到点扫描发送，标志位可控停止。"""

from __future__ import annotations

import logging
import threading

from PyQt5.QtCore import QThread

from ..utils import config
from ..core import scheduler

logger = logging.getLogger(__name__)


class ServiceThread(QThread):
    """进程内常驻服务线程。

    队列在 SQLite，配置在进程内共享：scheduler 每轮读取 settings，配置改动
    实时生效；停止通过 stop() 置标志位，最多等一轮扫描即可退出。
    """

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self._stop = threading.Event()

    def run(self) -> None:
        logger.info("监考提醒服务已启动。")
        try:
            scheduler.queue_reminders()
        except Exception:
            logger.exception("启动时重建提醒队列失败")

        while not self._stop.is_set():
            try:
                scheduler.process_due()
            except Exception:
                logger.exception("本轮发送处理异常")
            # 用 wait 代替 sleep：置位后能立即退出，不必等满一个间隔
            self._stop.wait(config.POLL_INTERVAL_SECONDS)

    def stop(self, timeout_ms: int = 8000) -> None:
        self._stop.set()
        self.wait(timeout_ms)
        logger.info("提醒服务已停止。")
