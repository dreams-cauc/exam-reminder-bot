# -*- coding: utf-8 -*-
"""数据导入：教务 Excel、教师名册 JSON。

Excel 按表头名取列，不依赖列序号（导出表可能增删列）。
姓名解析到 teachers 表拿钉钉ID；解析不到的姓名不提醒、不推送管理员，
只登记在监考名单里，供同场监考教师互知。
"""

from __future__ import annotations

import argparse
import json
import logging
import sys
from dataclasses import dataclass, field
from pathlib import Path

import pandas as pd

if __package__:
    from . import config, db, parsing
else:
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
    from src import config, db, parsing


logger = logging.getLogger(__name__)

# 表头名 -> 内部字段名。未知表头忽略，缺必需表头才报错。
COLUMN_ALIASES: dict[str, str] = {
    "课程": "raw_course",
    "考试时间": "raw_time",
    "考场校区": "campus",
    "楼号": "building",
    "考试教室": "room",
    "副监考1": "invigilator1",
    "副监考2": "invigilator2",
    "选课人数": "enrolled",
    "要求人数": "required",
    "主监考学院": "chief_college",
    "监考学院": "invigilator_college",
}

REQUIRED_COLUMNS = ("raw_course", "raw_time", "room")

# 监考教师列：顺序即座位标签
INVIGILATOR_COLUMNS = (
    ("invigilator1", "副监考1"),
    ("invigilator2", "副监考2"),
)


@dataclass
class ImportReport:
    source: str = ""
    rows_seen: int = 0
    rows_imported: int = 0
    rejected: list[tuple[int, str]] = field(default_factory=list)   # (Excel行号, 原因)
    missing_teachers: dict[str, list[str]] = field(default_factory=dict)
    warnings: list[str] = field(default_factory=list)

    def summary(self) -> str:
        lines = [
            f"导入文件: {self.source}",
            f"读取行数: {self.rows_seen}  成功导入: {self.rows_imported}  拒收: {len(self.rejected)}",
        ]
        if self.rejected:
            lines.append("拒收明细:")
            lines.extend(f"  行{row}: {reason}" for row, reason in self.rejected)
        if self.missing_teachers:
            lines.append("名册未命中（不提醒、不推送，仅登记在监考名单）:")
            for name, where in self.missing_teachers.items():
                lines.append(f"  {name}  <- {', '.join(where)}")
        if self.warnings:
            lines.append("警告:")
            lines.extend(f"  {w}" for w in self.warnings)
        return "\n".join(lines)


def load_user_json(path: Path | None = None) -> int:
    """把 user.json 载入 teachers 表。返回写入条数。

    JSON 形如 {"姓名": {"dingtalkid": "...", "role": "teacher"}, ...}
    系统运行不依赖 user.json，导入后以 SQLite 为准。
    """
    path = Path(path) if path else config.USER_JSON_PATH
    if not path.exists():
        raise FileNotFoundError(f"未找到用户映射文件: {path}")

    raw = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(raw, dict):
        raise ValueError(f"user.json 顶层应为对象，实际为 {type(raw).__name__}")

    count = 0
    no_mobile: list[str] = []
    with db.session() as conn:
        for name, info in raw.items():
            name = parsing.normalize_name(name)
            if not name:
                continue
            if not isinstance(info, dict):
                raise ValueError(f"user.json 中 {name!r} 的值应为对象")
            dingtalkid = str(info.get("dingtalkid", "")).strip()
            mobile = parsing.normalize_mobile(info.get("mobile", ""))
            role = str(info.get("role", "teacher")).strip() or "teacher"
            if not dingtalkid and not mobile:
                raise ValueError(
                    f"user.json 中 {name!r} 至少要给 dingtalkid 或 mobile 之一"
                )
            if not mobile:
                no_mobile.append(name)
            db.upsert_teacher(conn, name, dingtalkid, role, mobile=mobile)
            count += 1

    logger.info("user.json 导入完成：%d 名教师 -> %s", count, config.DB_PATH)
    if no_mobile:
        logger.warning(
            "%d 名教师未登记手机号，他们只能收到群消息、无法被真正 @：%s",
            len(no_mobile), "、".join(no_mobile),
        )
    return count


def _locate_header(df: pd.DataFrame) -> tuple[int, dict[str, int]]:
    """找到表头行，建立「内部字段名 -> 列下标」映射。

    有些导出表首行是标题行，所以扫前 10 行找同时含「课程」和「考试时间」的行。
    用列下标而非列名映射，规避 pandas 默认把首行当表头的歧义。
    """
    for idx in range(min(len(df), 10)):
        values = [parsing.normalize_text(v) for v in df.iloc[idx].tolist()]
        if "课程" in values and "考试时间" in values:
            mapping: dict[str, int] = {}
            for col_idx, header in enumerate(values):
                if not header:
                    continue
                field_name = COLUMN_ALIASES.get(header)
                if field_name:
                    mapping[field_name] = col_idx
            return idx, mapping
    raise ValueError("未找到表头行：需要同时包含「课程」与「考试时间」列")


def import_excel(path: Path | str) -> ImportReport:
    """导入监考安排 Excel。返回导入报告，拒收行不丢弃而是记入报告。"""
    path = Path(path)
    if not path.exists():
        raise FileNotFoundError(f"未找到 Excel 文件: {path}")

    df = pd.read_excel(path, sheet_name=0, header=None, dtype=str)
    df = df.fillna("")

    header_idx, colmap = _locate_header(df)
    missing = [c for c in REQUIRED_COLUMNS if c not in colmap]
    if missing:
        raise ValueError(f"Excel 缺少必需列: {missing}（当前映射: {sorted(colmap)}）")

    report = ImportReport(source=str(path))
    body = df.iloc[header_idx + 1:].reset_index(drop=True)

    def cell(row: pd.Series, field_name: str) -> str:
        col_idx = colmap.get(field_name)
        if col_idx is None:
            return ""
        return parsing.normalize_text(row.iloc[col_idx])

    # 先建名册索引，一次查库
    with db.session() as conn:
        known = {
            row["name"]
            for row in conn.execute("SELECT name FROM teachers")
        }

        for offset, row in body.iterrows():
            excel_row = int(offset) + header_idx + 2   # 1-based，含表头行

            if not any(str(v).strip() for v in row.tolist()):
                continue   # 整行空白
            report.rows_seen += 1

            try:
                course_code, course_name = parsing.split_course(cell(row, "raw_course"))
                et = parsing.parse_exam_time(cell(row, "raw_time"))
                room = parsing.normalize_text(cell(row, "room"))
                if not room:
                    raise parsing.ParseError("考试教室为空")
            except parsing.ParseError as exc:
                report.rejected.append((excel_row, str(exc)))
                continue

            exam_fields = {
                "course_code": course_code,
                "course_name": course_name,
                "raw_course": cell(row, "raw_course"),
                "raw_time": cell(row, "raw_time"),
                "week_num": et.week_num,
                "weekday_num": et.weekday_num,
                "exam_date": et.exam_date,
                "start_time": et.start_time,
                "end_time": et.end_time,
                "campus": cell(row, "campus"),
                "building": cell(row, "building"),
                "room": room,
                "enrolled": parsing.parse_int(cell(row, "enrolled")),
                "required": parsing.parse_int(cell(row, "required")),
                "chief_college": cell(row, "chief_college"),
                "invigilator_college": cell(row, "invigilator_college"),
                "source_file": path.name,
            }
            exam_id = db.insert_exam(conn, exam_fields)

            seats: list[tuple[str, str, int]] = []
            for field_name, seat_label in INVIGILATOR_COLUMNS:
                name = parsing.normalize_name(cell(row, field_name))
                if not name:
                    continue   # 副监考2 允许为空
                resolved = 1 if name in known else 0
                seats.append((seat_label, name, resolved))
                if not resolved:
                    report.missing_teachers.setdefault(name, []).append(
                        f"行{excel_row}"
                    )
            if not seats:
                report.rejected.append((excel_row, "没有任何监考教师（副监考1/副监考2 均为空）"))
                continue

            db.set_assignments(conn, exam_id, seats)
            report.rows_imported += 1

    logger.info("Excel 导入完成:\n%s", report.summary())
    return report


def main(argv: list[str] | None = None) -> int:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(name)-10s %(levelname)-8s %(message)s",
    )
    parser = argparse.ArgumentParser(description="导入教师名册与监考安排")
    parser.add_argument("excel_path", nargs="?", help="教务导出的 .xlsx 文件路径（可省略）")
    parser.add_argument(
        "--json", dest="json_path", default=None,
        help="user.json 路径，默认项目根目录下的 user.json",
    )
    parser.add_argument(
        "--no-queue", dest="no_queue", action="store_true",
        help="只导数据，不重建提醒队列",
    )
    args = parser.parse_args(argv)

    config.ensure_dirs()
    try:
        count = load_user_json(Path(args.json_path) if args.json_path else None)
    except (FileNotFoundError, ValueError) as exc:
        logging.error("名册导入失败：%s", exc)
        return 1
    print(f"已导入 {count} 名教师 -> {config.DB_PATH}")

    if not args.excel_path:
        return 0

    try:
        report = import_excel(Path(args.excel_path))
    except (FileNotFoundError, ValueError) as exc:
        logging.error("Excel 导入失败：%s", exc)
        return 1
    print("\n" + "=" * 60)
    print(report.summary())
    print("=" * 60)

    if not args.no_queue:
        from src import scheduler
        print(f"\n提醒队列重建：{scheduler.queue_reminders()}")

    return 1 if report.rejected else 0


if __name__ == "__main__":
    sys.exit(main())
