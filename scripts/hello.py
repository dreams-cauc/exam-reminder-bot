# !/usr/bin/env python
"""
机器人打招呼：

用法：
    python scripts/hello.py                 # 发送到群
    python scripts/hello.py --dry-run       # 只打印正文，不真正调钉钉
"""

from __future__ import annotations

import argparse
import logging
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from src.utils import config, dingtalk, log  # noqa: E402
from src.core import greeting  # noqa: E402


def setup_logger() -> None:
    log.setup()


def main() -> int:
    parser = argparse.ArgumentParser(description="机器人打招呼")
    parser.add_argument("--dry-run", action="store_true", help="只打印正文，不发送")
    args = parser.parse_args()

    setup_logger()
    config.ensure_dirs()

    if not config.WEBHOOK_URL or not config.SECRET:
        if not args.dry_run:
            logging.error(".env 缺少 webhook_url / secret，无法发送。可用 --dry-run 先验证。")
            return 1

    try:
        result = greeting.send_hello(dry_run=args.dry_run)
    except dingtalk.DingTalkError as exc:
        logging.error("打招呼失败：%s", exc)
        return 1

    if result is not None and not result.ok:
        return 1
    if args.dry_run:
        print("\n（dry-run）以上为打招呼正文，未发送")
    return 0


if __name__ == "__main__":
    sys.exit(main())
