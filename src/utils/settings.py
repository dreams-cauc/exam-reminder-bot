# -*- coding: utf-8 -*-
"""
用户配置单一来源：AppData/ExamMonitor/setting.json。

钉钉凭证与提醒参数（开关、时刻、话术模板）都由用户在界面里配置并手动保存，
代码不从 .env 自动加载任何参数；打包后同样由用户自行填写。运行期通过单例
`settings` 读写，`save()` 显式落盘；`ensure_initialized()` 在应用启动时
调用一次，缺失配置时写入默认值建档。
"""

from __future__ import annotations

import copy
import json
import logging
from pathlib import Path

from . import config

logger = logging.getLogger(__name__)

# 提醒话术模板默认值。运行期以 setting.json 为准，这里只作首次运行的兜底，
# 用户自定义并保存后系统直接读 json 里的模板发送。
_DEFAULT_DAY_BEFORE_TEMPLATE = """【📅 监考提醒】 {at_teacher}

老师您好，您明天的监考信息如下
📚 科目：{course}
🕘 时间：{time}
📍 教室：{location}
👥 老师：{proctor_teacher}
======================

"""

_DEFAULT_T2H_TEMPLATE = """【⏰ 监考提醒】 {at_teacher}

老师您好，您的监考已不足两小时
📚 科目：{course}
🕘 时间：{time}
📍 教室：{location}
👥 老师：{proctor_teacher}
======================

"""


def _deep_merge(base: dict, override: dict) -> dict:
    """把 override 递归合入 base 的副本，缺失字段用 base 补齐。"""
    result = copy.deepcopy(base)
    for key, value in override.items():
        if isinstance(value, dict) and isinstance(result.get(key), dict):
            result[key] = _deep_merge(result[key], value)
        else:
            result[key] = value
    return result


DEFAULTS: dict = {
    "dingtalk": {
        "webhook_url": "",
        "secret": "",
    },
    "reminder": {
        "day_before_enabled": True,
        "day_before_time": f"{config.DAY_BEFORE_HOUR:02d}:{config.DAY_BEFORE_MINUTE:02d}",
        "day_before_template": _DEFAULT_DAY_BEFORE_TEMPLATE,
        "t2h_enabled": True,
        "t2h_template": _DEFAULT_T2H_TEMPLATE,
    },
}


def _parse_hhmm(value: str) -> tuple[int, int]:
    """把 'HH:MM' 解析为 (时, 分)，解析失败回退到默认值。"""
    try:
        hour_s, minute_s = str(value).strip().split(":", 1)
        return int(hour_s), int(minute_s)
    except (ValueError, AttributeError):
        return config.DAY_BEFORE_HOUR, config.DAY_BEFORE_MINUTE


def _read_file(path: Path) -> dict:
    """读取配置 JSON；缺失或损坏时返回空字典（走默认值）。"""
    if not path.exists():
        return {}
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (ValueError, OSError) as exc:
        logger.warning("读取配置失败，使用默认值：%s", exc)
        return {}
    return raw if isinstance(raw, dict) else {}


class _Settings:
    """内存中的配置快照；访问即懒加载，落盘由 save() 显式触发。"""

    def __init__(self) -> None:
        self._data: dict | None = None

    # -- 生命周期 -------------------------------------------------
    def ensure_initialized(self) -> None:
        """加载配置；首次运行（无配置文件）时写入默认值建档。"""
        if self._data is not None:
            return
        config.ensure_dirs()
        exists = config.SETTINGS_PATH.exists()
        self._data = _deep_merge(DEFAULTS, _read_file(config.SETTINGS_PATH))
        if not exists:
            self.save()

    def save(self) -> None:
        """把当前内存配置写入 setting.json。"""
        config.ensure_dirs()
        config.SETTINGS_PATH.write_text(
            json.dumps(self._data, ensure_ascii=False, indent=4), encoding="utf-8"
        )

    def update(
        self, *, dingtalk: dict | None = None, reminder: dict | None = None
    ) -> None:
        """合并更新配置块（不自动落盘，需显式调用 save）。"""
        self.ensure_initialized()
        if dingtalk:
            self._data["dingtalk"].update(dingtalk)
        if reminder:
            self._data["reminder"].update(reminder)

    def snapshot(self) -> dict:
        """返回配置深拷贝，供界面载入。"""
        self.ensure_initialized()
        return copy.deepcopy(self._data)

    # -- 钉钉 -----------------------------------------------------
    @property
    def webhook_url(self) -> str:
        self.ensure_initialized()
        return str(self._data["dingtalk"].get("webhook_url", "")).strip()

    @property
    def secret(self) -> str:
        self.ensure_initialized()
        return str(self._data["dingtalk"].get("secret", "")).strip()

    # -- 提醒 -----------------------------------------------------
    @property
    def day_before_enabled(self) -> bool:
        self.ensure_initialized()
        return bool(self._data["reminder"].get("day_before_enabled", True))

    @property
    def day_before_time(self) -> str:
        self.ensure_initialized()
        return str(self._data["reminder"].get("day_before_time", "18:00"))

    @property
    def day_before_hour(self) -> int:
        return _parse_hhmm(self.day_before_time)[0]

    @property
    def day_before_minute(self) -> int:
        return _parse_hhmm(self.day_before_time)[1]

    @property
    def day_before_template(self) -> str:
        self.ensure_initialized()
        return str(self._data["reminder"].get("day_before_template", _DEFAULT_DAY_BEFORE_TEMPLATE))

    @property
    def t2h_enabled(self) -> bool:
        self.ensure_initialized()
        return bool(self._data["reminder"].get("t2h_enabled", True))

    @property
    def t2h_template(self) -> str:
        self.ensure_initialized()
        return str(self._data["reminder"].get("t2h_template", _DEFAULT_T2H_TEMPLATE))


settings = _Settings()
