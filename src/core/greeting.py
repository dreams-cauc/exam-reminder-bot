# -*- coding: utf-8 -*-

from __future__ import annotations

import logging

from ..utils import dingtalk
from . import messages

logger = logging.getLogger(__name__)


def send_hello(*, dry_run: bool = False) -> dingtalk.SendResult | None:
    """发一条 @所有人 的打招呼消息；dry_run 只打印不发送。"""
    body = messages.build_hello_message()
    if dry_run:
        logger.info("[dry-run] 打招呼:\n%s", body)
        return None
    result = dingtalk.send_text(body, is_at_all=True)
    if result.ok:
        logger.info("打招呼已发送")
    else:
        logger.error("打招呼发送失败：%s", result.error_text)
    return result
