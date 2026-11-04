# -*- coding: utf-8 -*-
"""钉钉自定义机器人发送。（HMAC-SHA256 加签 + text 消息）"""

from __future__ import annotations

import base64
import hashlib
import hmac
import logging
import time
import urllib.parse
from dataclasses import dataclass, field

import requests

from . import config

logger = logging.getLogger(__name__)


class DingTalkError(RuntimeError):
    """发送失败（网络异常或钉钉返回非 0 errcode）。"""


@dataclass
class SendResult:
    ok: bool
    errcode: int = -1
    errmsg: str = ""
    raw: dict = field(default_factory=dict)

    @property
    def error_text(self) -> str:
        return f"errcode={self.errcode} errmsg={self.errmsg}"


def _sign(secret: str) -> tuple[str, str]:
    """返回 (timestamp, sign)，算法与官方示例一致。"""
    timestamp = str(round(time.time() * 1000))
    string_to_sign = f"{timestamp}\n{secret}"
    hmac_code = hmac.new(
        secret.encode("utf-8"),
        string_to_sign.encode("utf-8"),
        digestmod=hashlib.sha256,
    ).digest()
    sign = urllib.parse.quote_plus(base64.b64encode(hmac_code))
    return timestamp, sign


def _build_url(webhook_url: str, secret: str) -> str:
    timestamp, sign = _sign(secret)
    # webhook_url 可能已带 ?access_token=，需用 & 续接
    sep = "&" if "?" in webhook_url else "?"
    return f"{webhook_url}{sep}timestamp={timestamp}&sign={sign}"


def send_text(
    content: str,
    at_user_ids: list[str] | None = None,
    at_mobiles: list[str] | None = None,
    is_at_all: bool = False,
    *,
    webhook_url: str | None = None,
    secret: str | None = None,
    timeout: float = 10.0,
) -> SendResult:
    """发送一条 text 消息到群。

    content      消息正文
    at_user_ids  要 @ 的钉钉用户ID列表（正文里通常也要出现 @ID 才会高亮）
    at_mobiles   要 @ 的手机号列表
    is_at_all    是否 @所有人
    """
    webhook_url = (webhook_url if webhook_url is not None else config.WEBHOOK_URL).strip()
    secret = (secret if secret is not None else config.SECRET).strip()
    if not webhook_url or not secret:
        raise DingTalkError(".env 中缺少 webhook_url 或 secret")

    url = _build_url(webhook_url, secret)
    body = {
        "at": {
            "isAtAll": is_at_all,
            "atUserIds": at_user_ids or [],
            "atMobiles": at_mobiles or [],
        },
        "text": {"content": content},
        "msgtype": "text",
    }
    headers = {"Content-Type": "application/json"}

    try:
        resp = requests.post(url, json=body, headers=headers, timeout=timeout)
        resp.raise_for_status()
        payload = resp.json()
    except requests.RequestException as exc:
        raise DingTalkError(f"钉钉请求失败：{exc}") from exc
    except ValueError as exc:
        raise DingTalkError(f"钉钉响应非 JSON：{resp.text[:200]}") from exc

    errcode = int(payload.get("errcode", -1))
    result = SendResult(
        ok=(errcode == 0),
        errcode=errcode,
        errmsg=str(payload.get("errmsg", "")),
        raw=payload,
    )
    if result.ok:
        logger.info("钉钉发送成功：%s", content.splitlines()[0] if content else "")
    else:
        logger.warning("钉钉发送失败：%s", result.error_text)
    return result
