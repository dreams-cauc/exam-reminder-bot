# -*- coding: utf-8 -*-
"""常驻服务主循环。

队列在 SQLite 里，APScheduler 之类的内存调度不参与状态：
到点扫描发送、休眠。休眠或重启期间积压的提醒会在下一轮被捡起。
"""

from __future__ import annotations

import logging
import time

from ..utils import config
from . import scheduler

logger = logging.getLogger(__name__)


def run_loop(*, dry_run: bool = False, interval: int | None = None) -> None:
    """持续运行。Ctrl+C 正常退出。"""
    interval = interval or config.POLL_INTERVAL_SECONDS
    logger.info("监考提醒服务已启动。")
    # 启动即重建一次队列，保证库里考试变化能立刻反映到提醒
    scheduler.queue_reminders()

    try:
        while True:
            try:
                scheduler.process_due(dry_run=dry_run)
            except Exception:  # noqa: BLE001 - 单轮失败不能拖垮服务
                logger.exception("本轮发送处理异常，%ds 后重试", interval)
            time.sleep(interval)
    except KeyboardInterrupt:
        logger.info("收到中断，服务退出")
