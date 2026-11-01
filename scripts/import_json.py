# !/usr/bin/env python
"""
用法：
    python scripts/import_json.py                 # 默认读项目根目录 user.json
    python scripts/import_json.py --json json/path/user.json        # 导入指定的json
"""

from __future__ import annotations

import argparse
import logging
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from src import config, importers  # noqa: E402


def setup_logger() -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(name)-10s %(levelname)-8s %(message)s [%(filename)s:%(lineno)d]",
    )


def main() -> int:
    parser = argparse.ArgumentParser(description="导入教师名册 JSON 到 SQLite")
    parser.add_argument(
        "--json", dest="json_path", default=str(config.USER_JSON_PATH),
        help="user.json 路径，默认项目根目录下的 user.json",
    )
    args = parser.parse_args()

    setup_logger()
    config.ensure_dirs()

    try:
        count = importers.load_user_json(Path(args.json_path))
    except (FileNotFoundError, ValueError) as exc:
        logging.error("导入失败：%s", exc)
        return 1

    print(f"\n已导入 {count} 名教师 -> {config.DB_PATH}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
