# !/usr/bin/env python
"""启动监考提醒服务。

用法：
    python scripts/run.py                 # 常驻，每 30s 扫描一次待发提醒
    python scripts/run.py --once          # 只跑一轮后退出
    python scripts/run.py --dry-run       # 只打印要发的内容，不真正调钉钉
    python scripts/run.py --interval 60   # 自定义扫描间隔（秒）

队列在 SQLite 中，进程重启/休眠后积压的提醒会被下一轮捡起，不丢不重。
"""

from __future__ import annotations

import argparse
import logging
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from src.utils import config, log  # noqa: E402
from src.utils.settings import settings  # noqa: E402
from src.core import scheduler, service  # noqa: E402


def setup_logger() -> None:
    log.setup()


def main() -> int:
    parser = argparse.ArgumentParser(description="监考提醒服务")
    parser.add_argument("--once", action="store_true", help="只跑一轮后退出")
    parser.add_argument("--dry-run", action="store_true", help="只打印，不发送")
    parser.add_argument("--interval", type=int, default=None, help="扫描间隔秒数")
    parser.add_argument(
        "--skip-queue", action="store_true",
        help="启动时不重建提醒队列（默认会重建一次）",
    )
    args = parser.parse_args()

    setup_logger()
    settings.ensure_initialized()
    config.ensure_dirs()

    if not settings.webhook_url or not settings.secret:
        if not args.dry_run:
            logging.error("setting.json 缺少 webhook_url / secret，无法发送。可用 --dry-run 先验证链路。")
            return 1

    if args.once:
        if not args.skip_queue:
            scheduler.queue_reminders()
        sent = scheduler.process_due(dry_run=args.dry_run)
        print(f"\n本轮处理 {sent} 条提醒" + ("（dry-run）" if args.dry_run else ""))
        return 0

    if args.skip_queue:
        # 常驻模式下 service.run_loop 自己会建队列；skip 时改为裸循环
        logging.info("跳过启动时的队列重建")
        import time

        try:
            while True:
                try:
                    scheduler.process_due(dry_run=args.dry_run)
                except Exception:
                    logging.exception("本轮发送处理异常")
                time.sleep(args.interval or config.POLL_INTERVAL_SECONDS)
        except KeyboardInterrupt:
            logging.info("收到中断，服务退出")
        return 0

    service.run_loop(dry_run=args.dry_run, interval=args.interval)
    return 0


if __name__ == "__main__":
    sys.exit(main())
