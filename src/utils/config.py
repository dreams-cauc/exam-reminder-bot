# -*- coding: utf-8 -*-
"""
全局配置：路径、时区、钉钉凭证(.env)、提醒时刻参数。
"""

from __future__ import annotations

import os
from pathlib import Path
from zoneinfo import ZoneInfo

from dotenv import load_dotenv

PROJECT_ROOT = Path(__file__).resolve().parents[2]
load_dotenv(PROJECT_ROOT / ".env")


def _clean(value: str | None) -> str:
    """去掉配置值两端的空白与引号"""
    if not value:
        return ""
    return value.strip().strip("\"' ")


DATA_DIR: Path = PROJECT_ROOT / "data"
DB_PATH: Path = DATA_DIR / "monitor.db"
USER_JSON_PATH: Path = PROJECT_ROOT / "user.json"

WEBHOOK_URL: str = _clean(os.getenv("webhook_url"))
SECRET: str = _clean(os.getenv("secret"))
DINGTALK_USERID: str = _clean(os.getenv("dingtalk_userid"))

TZ_NAME: str = "Asia/Shanghai"

# 考试前一天 18:00
DAY_BEFORE_HOUR: int = 18
DAY_BEFORE_MINUTE: int = 0

# 开考前 2 小时
T2H_OFFSET_HOURS: int = 2

# 提醒类型常量
REMINDER_DAY_BEFORE: str = "day_before"
REMINDER_T2H: str = "t2h"

# 轮询发送队列轮询间隔（秒）。SQLite 即队列，此处只是心跳。
POLL_INTERVAL_SECONDS: int = 30

# 钉钉发送重试：网络/超时等异常时的最大尝试次数与重试间隔（秒）
DINGTALK_MAX_ATTEMPTS: int = 3
DINGTALK_RETRY_DELAY_SECONDS: int = 10


def ensure_dirs() -> None:
    """创建运行所需的目录。"""
    DATA_DIR.mkdir(parents=True, exist_ok=True)


def tz() -> ZoneInfo:
    """上海时区对象。全系统时刻计算一律走这里。"""
    return ZoneInfo(TZ_NAME)
