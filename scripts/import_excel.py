# !/usr/bin/env python
"""
导入教务监考安排 Excel，展开监考关系并重建提醒队列。

用法：
    python scripts/import_excel.py xlsx/path/table.xlsx
    python scripts/import_excel.py xlsx/path/table.xlsx --no-queue      # 只导数据，不重建提醒

解析失败的行进拒收清单，不静默丢弃。
"""

from __future__ import annotations

import argparse
import logging
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from src.utils import config, log  # noqa: E402
from src.core import importers, scheduler  # noqa: E402


def setup_logger() -> None:
    log.setup()


def main() -> int:
    parser = argparse.ArgumentParser(description="导入监考安排 Excel")
    parser.add_argument("excel_path", help="教务导出的 .xlsx 文件路径")
    parser.add_argument(
        "--no-queue", dest="no_queue", action="store_true",
        help="只导入数据，不重建提醒队列",
    )
    args = parser.parse_args()

    setup_logger()
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
            "若需要提醒这些教师，请用 scripts/import_json.py 补齐 user.json 后重导。"
        )

    if not args.no_queue:
        stats = scheduler.queue_reminders()
        print(f"\n提醒队列重建：{stats}")

    return 1 if report.rejected else 0


if __name__ == "__main__":
    sys.exit(main())
