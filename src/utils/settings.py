# -*- coding: utf-8 -*-
"""
用户配置单一来源：AppData/ExamMonitor/setting.json。

钉钉凭证与提醒参数（开关、时刻、话术模板）都在此文件里；首次运行时把
历史 .env 里的钉钉凭证迁入，之后 .env 不再被读取。运行期通过单例
`settings` 读写，`save()` 显式落盘；`ensure_initialized()` 在应用启动时
调用一次，完成迁移与建档。
"""

from __future__ import annotations

import copy
import json
import logging
from pathlib import Path

from dotenv import dotenv_values

from . import config
from .prompt import daybefore_invigilator_msg, t2h_invigilator_msg

logger = logging.getLogger(__name__)


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
        "day_before_template": daybefore_invigilator_msg,
        "t2h_enabled": True,
        "t2h_template": t2h_invigilator_msg,
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


def _migrate_env(data: dict) -> bool:
    """把历史 .env 中的钉钉凭证迁入（仅当目标为空时），返回是否发生变更。"""
    env_path = config.PROJECT_ROOT / ".env"
    if not env_path.exists():
        return False
    env = dotenv_values(env_path)
    dingtalk = data.setdefault("dingtalk", {})
    changed = False
    if not str(dingtalk.get("webhook_url", "")).strip():
        value = str(env.get("webhook_url", "") or "").strip()
        if value:
            dingtalk["webhook_url"] = value
            changed = True
    if not str(dingtalk.get("secret", "")).strip():
        value = str(env.get("secret", "") or "").strip()
        if value:
            dingtalk["secret"] = value
            changed = True
    return changed


class _Settings:
    """内存中的配置快照；访问即懒加载，落盘由 save() 显式触发。"""

    def __init__(self) -> None:
        self._data: dict | None = None

    # -- 生命周期 -------------------------------------------------
    def ensure_initialized(self) -> None:
        """加载配置；首次运行（无配置文件）时迁移 .env 并落盘。"""
        if self._data is not None:
            return
        config.ensure_dirs()
        exists = config.SETTINGS_PATH.exists()
        self._data = _deep_merge(DEFAULTS, _read_file(config.SETTINGS_PATH))
        if not exists:
            _migrate_env(self._data)
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
        return str(self._data["reminder"].get("day_before_template", daybefore_invigilator_msg))

    @property
    def t2h_enabled(self) -> bool:
        self.ensure_initialized()
        return bool(self._data["reminder"].get("t2h_enabled", True))

    @property
    def t2h_template(self) -> str:
        self.ensure_initialized()
        return str(self._data["reminder"].get("t2h_template", t2h_invigilator_msg))


settings = _Settings()
