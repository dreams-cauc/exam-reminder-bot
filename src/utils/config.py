# -*- coding: utf-8 -*-
"""
全局配置：运行目录、时区、提醒时刻与轮询的默认参数。

运行期数据（SQLite 数据库与用户配置）统一放在系统 AppData 目录下，
与源码、git 工作区隔离；用户可调参数落在 setting.json，由 settings 模块读写。
"""

from __future__ import annotations

import os
from pathlib import Path
from zoneinfo import ZoneInfo

PROJECT_ROOT = Path(__file__).resolve().parents[2]

# 应用名：AppData 目录名与配置文件名都基于它
APP_NAME = "ExamMonitor"


def _app_data_dir() -> Path:
    # Windows 下 APPDATA 即 Roaming；其它平台回退到 ~/.config
    base = os.getenv("APPDATA") or (Path.home() / ".config")
    return Path(base) / APP_NAME


APP_DATA_DIR: Path = _app_data_dir()
DATA_DIR: Path = APP_DATA_DIR / "data"
DB_PATH: Path = DATA_DIR / "monitor.db"
SETTINGS_PATH: Path = APP_DATA_DIR / "setting.json"

# 教师名册是用户手动放置的导入源文件，仍从项目根目录读取作为默认值
USER_JSON_PATH: Path = PROJECT_ROOT / "user.json"

TZ_NAME: str = "Asia/Shanghai"

# 提醒时刻默认参数（可在 setting.json 中覆盖）
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
