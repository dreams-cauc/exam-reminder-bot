# -*- coding: utf-8 -*-
"""核心业务逻辑（包）。

    messages    消息正文拼装（依赖 utils.prompt）
    greeting    机器人打招呼
    importers   教务 Excel / 教师名册 JSON 导入
    scheduler   提醒时刻计算、按考场整理正文、发送队列
    service     轮询发送主循环
"""

from __future__ import annotations
