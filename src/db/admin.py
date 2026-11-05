# -*- coding: utf-8 -*-
"""面向界面的读写：视图行数据、单元格写回、删除、统计与清理。

界面展示的是三张视图（不含 UUID），编辑却要落到基表；本模块返回的行里
以 `_` 开头携带隐藏主键，供界面按列映射回对应的基表字段写回。
"""

from __future__ import annotations

import sqlite3

from .connection import now_str
from .reminders import (
    STATUS_PENDING,
    STATUS_SENT,
    STATUS_EXPIRED,
    STATUS_CANCELLED,
)

_VALID_ROLES = {"teacher", "admin", "developer"}


# ---------------------------------------------------------------------------
# 视图数据查询（返回列表，行内同时携带隐藏主键）
# ---------------------------------------------------------------------------

def list_exam_rows(conn: sqlite3.Connection) -> list[dict]:
    """考试全貌：课程/考场/时间段，附课程、考场、时间段、考试主键。"""
    rows = conn.execute(
        "SELECT e.id AS _exam_id, c.id AS _course_id, r.id AS _room_id, s.id AS _slot_id,"
        " c.name AS 课程名, r.name AS 考场名, s.exam_date AS 考试日期,"
        " s.start_time AS 开始时间, s.end_time AS 结束时间"
        " FROM exams e"
        " JOIN courses c ON c.id = e.course_id"
        " JOIN rooms r ON r.id = e.room_id"
        " JOIN exam_slots s ON s.id = e.slot_id"
        " ORDER BY s.exam_date, s.start_time, r.name"
    ).fetchall()
    return [dict(r) for r in rows]


def list_invigilator_rows(conn: sqlite3.Connection) -> list[dict]:
    """监考人员全貌：座位/教师快照 + 名册信息（名册外教师手机号等为空）。"""
    rows = conn.execute(
        "SELECT i.id AS _invigilator_id, e.id AS _exam_id, c.id AS _course_id,"
        " r.id AS _room_id, s.id AS _slot_id, i.teacher_id AS _teacher_id,"
        " c.name AS 课程名, r.name AS 考场名, s.exam_date AS 考试日期,"
        " s.start_time AS 开始时间, s.end_time AS _end_time,"
        " i.seat_label AS 监考角色, i.teacher_name AS 教师姓名,"
        " COALESCE(t.mobile, '') AS 手机号, COALESCE(t.dingtalk_id, '') AS 钉钉号,"
        " COALESCE(t.role, '') AS 角色"
        " FROM exam_invigilators i"
        " JOIN exams e ON e.id = i.exam_id"
        " JOIN courses c ON c.id = e.course_id"
        " JOIN rooms r ON r.id = e.room_id"
        " JOIN exam_slots s ON s.id = e.slot_id"
        " LEFT JOIN teachers t ON t.id = i.teacher_id"
        " ORDER BY s.exam_date, s.start_time, r.name, i.rowid"
    ).fetchall()
    return [dict(r) for r in rows]


def list_due_reminder_rows(conn: sqlite3.Connection) -> list[dict]:
    """待发提醒：一行一个收件人，附提醒主键（供整条取消）。"""
    rows = conn.execute(
        "SELECT r.id AS _reminder_id, r.id AS 提醒ID, r.reminder_type AS 提醒类型,"
        " r.scheduled_at AS 计划触发时间, r.message AS 消息内容,"
        " r.exception_message AS 异常消息, rr.teacher_name AS 教师姓名,"
        " rr.mobile AS 手机号"
        " FROM reminders r"
        " JOIN reminder_recipients rr ON rr.reminder_id = r.id"
        " WHERE r.status = ?"
        " ORDER BY r.scheduled_at, r.rowid, rr.rowid",
        (STATUS_PENDING,),
    ).fetchall()
    return [dict(r) for r in rows]


# ---------------------------------------------------------------------------
# 单元格写回（共享字典：课程名/考场名/时间段编辑会影响引用它们的其它行）
# ---------------------------------------------------------------------------

def update_course_name(conn: sqlite3.Connection, course_id: str, name: str) -> None:
    """改写课程字典中的课程名。"""
    conn.execute("UPDATE courses SET name = ? WHERE id = ?", (name, course_id))


def update_room_name(conn: sqlite3.Connection, room_id: str, name: str) -> None:
    """改写考场字典中的考场名。"""
    conn.execute("UPDATE rooms SET name = ? WHERE id = ?", (name, room_id))


def update_slot(
    conn: sqlite3.Connection,
    slot_id: str,
    *,
    exam_date: str,
    start_time: str,
    end_time: str,
) -> None:
    """改写时间段（日期/起止）；校验由表上的 CHECK/UNIQUE 约束兜底。"""
    conn.execute(
        "UPDATE exam_slots SET exam_date = ?, start_time = ?, end_time = ? WHERE id = ?",
        (exam_date, start_time, end_time, slot_id),
    )


def update_invigilator(
    conn: sqlite3.Connection,
    invigilator_id: str,
    *,
    seat_label: str | None = None,
    teacher_name: str | None = None,
) -> None:
    """改写监考安排的座位标签 / 教师姓名快照。"""
    if seat_label is not None:
        conn.execute(
            "UPDATE exam_invigilators SET seat_label = ? WHERE id = ?",
            (seat_label, invigilator_id),
        )
    if teacher_name is not None:
        conn.execute(
            "UPDATE exam_invigilators SET teacher_name = ? WHERE id = ?",
            (teacher_name, invigilator_id),
        )


def update_teacher(
    conn: sqlite3.Connection,
    teacher_id: str | None,
    *,
    mobile: str | None = None,
    dingtalk_id: str | None = None,
    role: str | None = None,
) -> None:
    """改写名册教师信息；名册外教师（teacher_id 为空）不可改。"""
    if not teacher_id:
        raise ValueError("该教师不在名册中，无法修改手机号/钉钉号/角色")
    if role is not None and role not in _VALID_ROLES:
        raise ValueError(f"非法角色 {role!r}，可选：teacher / admin / developer")
    if mobile is not None:
        conn.execute("UPDATE teachers SET mobile = ? WHERE id = ?", (mobile, teacher_id))
    if dingtalk_id is not None:
        conn.execute(
            "UPDATE teachers SET dingtalk_id = ? WHERE id = ?", (dingtalk_id, teacher_id)
        )
    if role is not None:
        conn.execute("UPDATE teachers SET role = ? WHERE id = ?", (role, teacher_id))


# ---------------------------------------------------------------------------
# 删除 / 取消 / 统计 / 清理
# ---------------------------------------------------------------------------

def delete_exam(conn: sqlite3.Connection, exam_id: str) -> None:
    """删除一场考试（监考安排随之级联删除）。"""
    conn.execute("DELETE FROM exams WHERE id = ?", (exam_id,))


def delete_invigilator(conn: sqlite3.Connection, invigilator_id: str) -> None:
    """删除一条监考安排。"""
    conn.execute("DELETE FROM exam_invigilators WHERE id = ?", (invigilator_id,))


def cancel_reminder(conn: sqlite3.Connection, reminder_id: str) -> None:
    """把一条待发提醒置为 cancelled（其收件人快照保留）。"""
    conn.execute(
        "UPDATE reminders SET status = ?, updated_at = ? WHERE id = ?",
        (STATUS_CANCELLED, now_str(), reminder_id),
    )


def get_stats(conn: sqlite3.Connection) -> dict[str, int]:
    """界面统计：监考任务数、待提醒数、已提醒数。"""
    task = conn.execute("SELECT COUNT(*) FROM exams").fetchone()[0]
    pending = conn.execute(
        "SELECT COUNT(*) FROM reminders WHERE status = ?", (STATUS_PENDING,)
    ).fetchone()[0]
    done = conn.execute(
        "SELECT COUNT(*) FROM reminders WHERE status = ?", (STATUS_SENT,)
    ).fetchone()[0]
    return {"task": task, "pending": pending, "done": done}


def cleanup_expired(conn: sqlite3.Connection) -> int:
    """删除已过期、已取消的提醒（收件人快照级联删除），返回删除条数。"""
    cur = conn.execute(
        "DELETE FROM reminders WHERE status IN (?, ?)",
        (STATUS_EXPIRED, STATUS_CANCELLED),
    )
    return cur.rowcount
