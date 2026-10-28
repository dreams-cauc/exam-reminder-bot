# -*- coding: utf-8 -*-
"""
monitor hello                机器人在群聊中打招呼
monitor iptjson --json PATH  没有数据库则建库并导入教师名册
monitor iptxlsx PATH         导入教师监考表（默认重建提醒队列）
monitor server               常驻，每 30s 扫描一次待发提醒

"""

from __future__ import annotations

import argparse
import logging
from pathlib import Path
from typing import Callable


def setup_logger() -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(name)-10s %(levelname)-8s %(message)s [%(filename)s:%(lineno)d]",
    )


def _cmd_hello(args: argparse.Namespace) -> int:
    from . import config, dingtalk, greeting

    if not config.WEBHOOK_URL or not config.SECRET:
        if not args.dry_run:
            logging.error(".env 缺少 webhook_url / secret，无法发送。可用 --dry-run 先验证。")
            return 1
    try:
        result = greeting.send_hello(dry_run=args.dry_run)
    except dingtalk.DingTalkError as exc:
        logging.error("打招呼失败：%s", exc)
        return 1
    return 0 if result is None or result.ok else 1


def _cmd_iptjson(args: argparse.Namespace) -> int:
    from . import config, importers

    config.ensure_dirs()
    try:
        count = importers.load_user_json(
            Path(args.json_path) if args.json_path else None
        )
    except (FileNotFoundError, ValueError) as exc:
        logging.error("导入失败：%s", exc)
        return 1
    print(f"\n已导入 {count} 名教师 -> {config.DB_PATH}")
    return 0


def _cmd_iptxlsx(args: argparse.Namespace) -> int:
    from . import config, importers, scheduler

    config.ensure_dirs()
    try:
        report = importers.import_excel(Path(args.excel_path))
    except (FileNotFoundError, ValueError) as exc:
        logging.error("导入失败：%s", exc)
        return 1

    print("\n" + "=" * 60)
    print(report.summary())
    print("=" * 60)
    if report.missing_teachers:
        print(
            "\n说明：上述教师不在名册中，系统不会提醒他们（仅登记在监考名单）。\n"
            "若需要提醒这些教师，请先用 iptjson 补齐名册后重导。"
        )
    if not args.no_queue:
        print(f"\n提醒队列重建：{scheduler.queue_reminders()}")
    return 1 if report.rejected else 0


def _cmd_server(args: argparse.Namespace) -> int:
    from . import config, scheduler, service

    config.ensure_dirs()
    if not config.WEBHOOK_URL or not config.SECRET:
        if not args.dry_run:
            logging.error(".env 缺少 webhook_url / secret，无法发送。可用 --dry-run 先验证。")
            return 1

    if args.once:
        if not args.skip_queue:
            scheduler.queue_reminders()
        sent = scheduler.process_due(dry_run=args.dry_run)
        print(f"\n本轮处理 {sent} 条提醒" + ("（dry-run）" if args.dry_run else ""))
        return 0

    if args.skip_queue:
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


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="monitor", description="监考提醒系统")
    sub = parser.add_subparsers(dest="command", required=True)

    p = sub.add_parser("hello", help="机器人在群聊中打招呼")
    p.add_argument("--dry-run", action="store_true", help="只打印正文，不发送")
    p.set_defaults(func=_cmd_hello)

    p = sub.add_parser("iptjson", help="没有数据库则建库并导入教师名册")
    p.add_argument(
        "--json", dest="json_path", default=None,
        help="user.json 路径，默认运行目录下的 user.json",
    )
    p.set_defaults(func=_cmd_iptjson)

    p = sub.add_parser("iptxlsx", help="导入教师监考表")
    p.add_argument("excel_path", help="教务导出的 .xlsx 文件路径")
    p.add_argument(
        "--no-queue", dest="no_queue", action="store_true",
        help="只导数据，不重建提醒队列",
    )
    p.set_defaults(func=_cmd_iptxlsx)

    p = sub.add_parser("server", help="常驻，每 30s 扫描一次待发提醒")
    p.add_argument("--once", action="store_true", help="只跑一轮后退出")
    p.add_argument("--dry-run", action="store_true", help="只打印，不发送")
    p.add_argument("--interval", type=int, default=None, help="扫描间隔秒数")
    p.add_argument(
        "--skip-queue", action="store_true",
        help="启动时不重建提醒队列（默认会重建一次）",
    )
    p.set_defaults(func=_cmd_server)

    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    setup_logger()
    func: Callable[[argparse.Namespace], int] = args.func
    return func(args)


if __name__ == "__main__":
    raise SystemExit(main())
