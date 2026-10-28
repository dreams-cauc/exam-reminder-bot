# -*- coding: utf-8 -*-
"""SQLite 表结构与访问层。

所有持久化状态落库：教师名册、考试场次、监考安排、提醒队列。
"""

from __future__ import annotations

import sqlite3
from collections.abc import Generator
from contextlib import contextmanager
from datetime import datetime
from typing import Any

from . import config

# 提醒状态机：pending -> sending -> sent | failed
# skipped / expired 由巡检时判定，cancelled 在重导入后由孤儿清理置位
STATUS_PENDING = "pending"
STATUS_SENDING = "sending"
STATUS_SENT = "sent"
STATUS_FAILED = "failed"
STATUS_EXPIRED = "expired"
STATUS_CANCELLED = "cancelled"

SCHEMA = """
PRAGMA foreign_keys = ON;

-- 教师名册：姓名 -> 钉钉账户ID + 手机号。姓名是主键，与 Excel 中的姓名列对齐。
-- 自定义机器人只能用手机号真正 @ 到人，故 mobile 是 @ 的依据；
-- dingtalkid 保留作为账号标识，兜底拼进正文供人读。
CREATE TABLE IF NOT EXISTS teachers (
    name        TEXT PRIMARY KEY,
    dingtalkid  TEXT NOT NULL DEFAULT '',
    mobile      TEXT NOT NULL DEFAULT '',
    role        TEXT NOT NULL DEFAULT 'teacher',
    imported_at TEXT NOT NULL
);

-- 一场考试 = 同课程 + 同日期 + 同起始时间；每个教室一行。
CREATE TABLE IF NOT EXISTS exams (
    id                INTEGER PRIMARY KEY AUTOINCREMENT,
    course_code       TEXT NOT NULL DEFAULT '',
    course_name       TEXT NOT NULL,
    raw_course        TEXT NOT NULL,
    raw_time          TEXT NOT NULL,
    week_num          INTEGER,
    weekday_num       INTEGER,
    exam_date         TEXT NOT NULL,          -- YYYY-MM-DD
    start_time        TEXT NOT NULL,          -- HH:MM
    end_time          TEXT NOT NULL,          -- HH:MM
    campus            TEXT NOT NULL DEFAULT '',
    building          TEXT NOT NULL DEFAULT '',
    room              TEXT NOT NULL,
    enrolled          INTEGER,
    required          INTEGER,
    chief_college     TEXT NOT NULL DEFAULT '',
    invigilator_college TEXT NOT NULL DEFAULT '',
    source_file       TEXT NOT NULL DEFAULT '',
    imported_at       TEXT NOT NULL,
    UNIQUE (course_code, course_name, exam_date, start_time, room)
);

-- 监考安排：一个考场一行主监考/副监考。
CREATE TABLE IF NOT EXISTS assignments (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    exam_id      INTEGER NOT NULL REFERENCES exams(id) ON DELETE CASCADE,
    seat_label   TEXT NOT NULL,          -- 副监考1 / 副监考2
    teacher_name TEXT NOT NULL,
    resolved     INTEGER NOT NULL DEFAULT 0,   -- 1 表示已在 teachers 中找到
    UNIQUE (exam_id, seat_label)
);

CREATE INDEX IF NOT EXISTS idx_assignments_exam ON assignments (exam_id);
CREATE INDEX IF NOT EXISTS idx_assignments_name ON assignments (teacher_name);

-- 提醒队列：一行 = 一条待发消息。同一时间段的多个考场合并进同一条。
CREATE TABLE IF NOT EXISTS reminders (
    id                INTEGER PRIMARY KEY AUTOINCREMENT,
    reminder_type     TEXT NOT NULL,      -- day_before | t2h
    group_key         TEXT NOT NULL,      -- 合并键：<日期>|<起始>|<结束>
    scheduled_at      TEXT NOT NULL,      -- 计划触发时刻（上海时区）
    course_code       TEXT NOT NULL DEFAULT '',
    course_name       TEXT NOT NULL,
    exam_date         TEXT NOT NULL,
    start_time        TEXT NOT NULL,
    end_time          TEXT NOT NULL,
    names_json        TEXT NOT NULL,      -- 消息里出现的监考教师姓名
    at_ids_json       TEXT NOT NULL DEFAULT '[]',   -- 可 @ 的手机号（钉钉自定义机器人只能按手机号@）
    missing_json      TEXT NOT NULL DEFAULT '[]',   -- 库内但 @ 失败的姓名（推送给管理员）
    message           TEXT NOT NULL,
    exception_message TEXT NOT NULL DEFAULT '',
    status            TEXT NOT NULL DEFAULT 'pending',
    attempts          INTEGER NOT NULL DEFAULT 0,
    last_error        TEXT NOT NULL DEFAULT '',
    sent_at           TEXT,
    created_at        TEXT NOT NULL,
    updated_at        TEXT NOT NULL,
    UNIQUE (reminder_type, group_key)
);

CREATE INDEX IF NOT EXISTS idx_reminders_due ON reminders (status, scheduled_at);
"""


def now_str() -> str:
    """上海时区当前时间，统一存成 'YYYY-MM-DD HH:MM:SS'。"""
    return datetime.now(config.tz()).strftime("%Y-%m-%d %H:%M:%S")


def _migrate(conn: sqlite3.Connection) -> None:
    """给旧库补列。CREATE TABLE IF NOT EXISTS 不会改已有表结构。"""
    cols = {
        row["name"]
        for row in conn.execute("PRAGMA table_info(teachers)")
    }
    if cols and "mobile" not in cols:
        conn.execute(
            "ALTER TABLE teachers ADD COLUMN mobile TEXT NOT NULL DEFAULT ''"
        )


def connect() -> sqlite3.Connection:
    """打开数据库连接，缺目录/缺表时自动建好。"""
    config.ensure_dirs()
    conn = sqlite3.connect(config.DB_PATH, timeout=30)
    conn.row_factory = sqlite3.Row
    conn.executescript(SCHEMA)
    _migrate(conn)
    return conn


@contextmanager
def session() -> Generator[sqlite3.Connection, None, None]:
    """事务上下文：正常提交，异常回滚。"""
    conn = connect()
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def get_teacher(conn: sqlite3.Connection, name: str) -> sqlite3.Row | None:
    return conn.execute(
        "SELECT * FROM teachers WHERE name = ?", (name,)
    ).fetchone()


def upsert_teacher(
    conn: sqlite3.Connection,
    name: str,
    dingtalkid: str,
    role: str = "teacher",
    mobile: str = "",
    imported_at: str | None = None,
) -> None:
    conn.execute(
        """
        INSERT INTO teachers (name, dingtalkid, mobile, role, imported_at)
        VALUES (?, ?, ?, ?, ?)
        ON CONFLICT(name) DO UPDATE SET
            dingtalkid  = excluded.dingtalkid,
            mobile      = excluded.mobile,
            role        = excluded.role,
            imported_at = excluded.imported_at
        """,
        (name, dingtalkid, mobile, role, imported_at or now_str()),
    )


def insert_exam(conn: sqlite3.Connection, fields: dict[str, Any]) -> int:
    """写入一场考试的一个考场，已存在则覆盖并返回原 id。"""
    cols = (
        "course_code", "course_name", "raw_course", "raw_time",
        "week_num", "weekday_num", "exam_date", "start_time", "end_time",
        "campus", "building", "room", "enrolled", "required",
        "chief_college", "invigilator_college", "source_file", "imported_at",
    )
    values = [fields.get(c) for c in cols]
    values[cols.index("imported_at")] = fields.get("imported_at") or now_str()
    placeholders = ", ".join("?" for _ in cols)
    updates = ", ".join(f"{c} = excluded.{c}" for c in cols if c != "imported_at")
    conn.execute(
        f"""
        INSERT INTO exams ({", ".join(cols)})
        VALUES ({placeholders})
        ON CONFLICT(course_code, course_name, exam_date, start_time, room)
        DO UPDATE SET {updates}
        """,
        values,
    )
    # 冲突更新时 lastrowid 不可靠，统一回查取主键
    row = conn.execute(
        """SELECT id FROM exams
           WHERE course_code = ? AND course_name = ?
             AND exam_date = ? AND start_time = ? AND room = ?""",
        (
            fields["course_code"], fields["course_name"],
            fields["exam_date"], fields["start_time"], fields["room"],
        ),
    ).fetchone()
    return int(row["id"])


def set_assignments(
    conn: sqlite3.Connection, exam_id: int, seats: list[tuple[str, str, int]]
) -> None:
    """覆盖某个考场的监考安排。seats: [(seat_label, teacher_name, resolved), ...]"""
    conn.execute("DELETE FROM assignments WHERE exam_id = ?", (exam_id,))
    conn.executemany(
        """INSERT INTO assignments (exam_id, seat_label, teacher_name, resolved)
           VALUES (?, ?, ?, ?)""",
        [(exam_id, label, name, resolved) for label, name, resolved in seats],
    )


def slot_groups(conn: sqlite3.Connection) -> list[sqlite3.Row]:
    """按考试时间段 (exam_date, start_time, end_time) 聚合出提醒批次。

    同一时间段可含多门课程、多个考场，整批合并成一条消息发出。
    """
    return conn.execute(
        """
        SELECT  exam_date, start_time, end_time
        FROM    exams
        GROUP BY exam_date, start_time, end_time
        ORDER BY exam_date, start_time, end_time
        """
    ).fetchall()


def rooms_for_slot(
    conn: sqlite3.Connection,
    exam_date: str,
    start_time: str,
    end_time: str,
) -> list[dict[str, Any]]:
    """取出该时间段的全部考场（一行考试记录 = 一个考场）及各自监考教师。

    教师按座位顺序（插入顺序）排列，同名只保留首次出现。
    resolved=1 表示姓名能在 teachers 名册中命中（可参与提醒），
    名册外教师同样返回，供监考名单展示，但不提醒。
    """
    exams = conn.execute(
        """
        SELECT id, course_code, course_name, campus, building, room
        FROM   exams
        WHERE  exam_date = ? AND start_time = ? AND end_time = ?
        ORDER BY id
        """,
        (exam_date, start_time, end_time),
    ).fetchall()
    rooms: list[dict[str, Any]] = []
    for e in exams:
        teachers = conn.execute(
            """
            SELECT a.teacher_name AS name,
                   COALESCE(t.dingtalkid, '') AS dingtalkid,
                   COALESCE(t.mobile, '')     AS mobile,
                   CASE WHEN t.name IS NULL THEN 0 ELSE 1 END AS resolved
            FROM   assignments a
            LEFT JOIN teachers t ON t.name = a.teacher_name
            WHERE  a.exam_id = ?
            GROUP BY a.teacher_name
            ORDER BY MIN(a.id)
            """,
            (e["id"],),
        ).fetchall()
        rooms.append({
            "course_code": e["course_code"],
            "course_name": e["course_name"],
            "campus": e["campus"],
            "building": e["building"],
            "room": e["room"],
            "teachers": [dict(t) for t in teachers],
        })
    return rooms


def upsert_reminder(
    conn: sqlite3.Connection, fields: dict[str, Any]
) -> tuple[int, str]:
    """写入/更新一条提醒。

    已发出（sent）的记录不再改写，避免重导入把历史提醒拉回待发队列。
    返回 (id, 动作)，动作取 created / updated / kept。
    """
    existing = conn.execute(
        "SELECT id, status FROM reminders WHERE reminder_type = ? AND group_key = ?",
        (fields["reminder_type"], fields["group_key"]),
    ).fetchone()
    ts = now_str()

    if existing is None:
        cols = [
            "reminder_type", "group_key", "scheduled_at",
            "course_code", "course_name", "exam_date", "start_time", "end_time",
            "names_json", "at_ids_json", "missing_json",
            "message", "exception_message",
            "status", "created_at", "updated_at",
        ]
        values = [
            fields["reminder_type"], fields["group_key"], fields["scheduled_at"],
            fields["course_code"], fields["course_name"],
            fields["exam_date"], fields["start_time"], fields["end_time"],
            fields["names_json"], fields["at_ids_json"], fields["missing_json"],
            fields["message"], fields["exception_message"],
            STATUS_PENDING, ts, ts,
        ]
        cur = conn.execute(
            f"INSERT INTO reminders ({', '.join(cols)}) VALUES ({', '.join('?' * len(cols))})",
            values,
        )
        return int(cur.lastrowid), "created"

    rid = int(existing["id"])
    if existing["status"] in (STATUS_SENT, STATUS_SENDING):
        return rid, "kept"

    conn.execute(
        """
        UPDATE reminders SET
            scheduled_at      = ?,
            course_code       = ?,
            course_name       = ?,
            exam_date         = ?,
            start_time        = ?,
            end_time          = ?,
            names_json        = ?,
            at_ids_json       = ?,
            missing_json      = ?,
            message           = ?,
            exception_message = ?,
            status            = ?,
            updated_at        = ?
        WHERE id = ?
        """,
        (
            fields["scheduled_at"], fields["course_code"], fields["course_name"],
            fields["exam_date"], fields["start_time"], fields["end_time"],
            fields["names_json"], fields["at_ids_json"], fields["missing_json"],
            fields["message"], fields["exception_message"],
            STATUS_PENDING, ts, rid,
        ),
    )
    return rid, "updated"


def cancel_orphan_reminders(conn: sqlite3.Connection, keep_group_keys: set[str]) -> int:
    """把已不存在对应考试的待发提醒置为 cancelled，防止发出过期/错误内容。

    keep_group_keys 收的是 reminders.group_key（<日期>|<起始>|<结束>），
    同一考试时间段的 day_before / t2h 共用同一个 group_key。
    已发出的记录只保留不动（审计需要）。
    """
    rows = conn.execute(
        "SELECT id, group_key FROM reminders WHERE status IN (?, ?)",
        (STATUS_PENDING, STATUS_FAILED),
    ).fetchall()
    cancelled = 0
    ts = now_str()
    for row in rows:
        if row["group_key"] in keep_group_keys:
            continue
        conn.execute(
            "UPDATE reminders SET status = ?, updated_at = ? WHERE id = ?",
            (STATUS_CANCELLED, ts, row["id"]),
        )
        cancelled += 1
    return cancelled


def fetch_due(conn: sqlite3.Connection, now: str) -> list[sqlite3.Row]:
    """取出到点且考试尚未结束的待发提醒。"""
    return conn.execute(
        """
        SELECT * FROM reminders
        WHERE  status = ?
          AND  scheduled_at <= ?
          AND  (exam_date || ' ' || end_time) > ?
        ORDER BY scheduled_at, id
        """,
        (STATUS_PENDING, now, now),
    ).fetchall()


def expire_overdue(conn: sqlite3.Connection, now: str) -> int:
    """考试已结束仍未发出的提醒置为 expired，避免事后打扰。"""
    cur = conn.execute(
        """
        UPDATE reminders
        SET    status = 'expired', updated_at = ?
        WHERE  status = ?
          AND  (exam_date || ' ' || end_time) <= ?
        """,
        (now, STATUS_PENDING, now),
    )
    return cur.rowcount


def cancel_stale_day_before(conn: sqlite3.Connection, today: str) -> int:
    """考试已进入当天后，作废还没发出的前一天提醒。

    「您明天的监考信息」话术只适用于考试前一天；进入当天（含两小时以内）
    改由 t2h 提醒负责，避免误导教师漏掉当天监考。
    """
    cur = conn.execute(
        """
        UPDATE reminders
        SET    status = ?, updated_at = ?
        WHERE  reminder_type = ?
          AND  status IN (?, ?)
          AND  exam_date <= ?
        """,
        (
            STATUS_CANCELLED, now_str(), config.REMINDER_DAY_BEFORE,
            STATUS_PENDING, STATUS_FAILED, today,
        ),
    )
    return cur.rowcount


def claim_reminder(conn: sqlite3.Connection, reminder_id: int) -> bool:
    """原子占用一条待发提醒，防止重复发送（含进程重启后的重复触发）。"""
    cur = conn.execute(
        """
        UPDATE reminders
        SET    status = ?, attempts = attempts + 1, updated_at = ?
        WHERE  id = ? AND status = ?
        """,
        (STATUS_SENDING, now_str(), reminder_id, STATUS_PENDING),
    )
    return cur.rowcount == 1


def mark_sent(conn: sqlite3.Connection, reminder_id: int) -> None:
    ts = now_str()
    conn.execute(
        "UPDATE reminders SET status = ?, sent_at = ?, last_error = '', updated_at = ? WHERE id = ?",
        (STATUS_SENT, ts, ts, reminder_id),
    )


def mark_failed(conn: sqlite3.Connection, reminder_id: int, error: str) -> None:
    conn.execute(
        "UPDATE reminders SET status = ?, last_error = ?, updated_at = ? WHERE id = ?",
        (STATUS_FAILED, error[:500], now_str(), reminder_id),
    )


def set_last_error(conn: sqlite3.Connection, reminder_id: int, error: str) -> None:
    """主消息已发出、附属通知失败时只记录，不回滚状态。"""
    conn.execute(
        "UPDATE reminders SET last_error = ?, updated_at = ? WHERE id = ?",
        (error[:500], now_str(), reminder_id),
    )
