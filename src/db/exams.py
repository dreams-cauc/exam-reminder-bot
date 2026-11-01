# -*- coding: utf-8 -*-
"""考试场次与监考安排的读写，以及提醒聚合所需的查询。"""

from __future__ import annotations

import sqlite3
from typing import Any

from .connection import new_id, now_str


def upsert_exam(
    conn: sqlite3.Connection,
    *,
    course_id: str,
    room_id: str,
    slot_id: str,
    enrolled: int | None,
    required: int | None,
    chief_college: str,
    invigilator_college: str,
    source_file: str,
) -> str:
    """写入/更新一个考场一场考试，返回考试 id。同一考场同一时段视为同一场。"""
    row = conn.execute(
        "SELECT id FROM exams WHERE room_id = ? AND slot_id = ?",
        (room_id, slot_id),
    ).fetchone()
    ts = now_str()
    if row:
        eid = row["id"]
        conn.execute(
            "UPDATE exams SET course_id = ?, enrolled = ?, required = ?, chief_college = ?,"
            " invigilator_college = ?, source_file = ?, updated_at = ? WHERE id = ?",
            (course_id, enrolled, required, chief_college, invigilator_college, source_file, ts, eid),
        )
        return eid

    eid = new_id()
    conn.execute(
        "INSERT INTO exams (id, course_id, room_id, slot_id, enrolled, required, chief_college,"
        " invigilator_college, source_file, created_at, updated_at)"
        " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (eid, course_id, room_id, slot_id, enrolled, required, chief_college,
         invigilator_college, source_file, ts, ts),
    )
    return eid


def set_invigilators(
    conn: sqlite3.Connection,
    exam_id: str,
    seats: list[tuple[str | None, str, str]],
) -> None:
    """覆盖某场考试的监考安排。seats: [(teacher_id, teacher_name, seat_label), ...]。

    teacher_id 为 None 表示名册外教师，仅保留姓名供展示。
    """
    conn.execute("DELETE FROM exam_invigilators WHERE exam_id = ?", (exam_id,))
    ts = now_str()
    conn.executemany(
        "INSERT INTO exam_invigilators (id, exam_id, teacher_id, teacher_name, seat_label, created_at)"
        " VALUES (?, ?, ?, ?, ?, ?)",
        [(new_id(), exam_id, tid, name, label, ts) for tid, name, label in seats],
    )


def list_slots(conn: sqlite3.Connection) -> list[sqlite3.Row]:
    """返回仍有考试的时间段，按时间升序。"""
    return conn.execute(
        "SELECT id, exam_date, start_time, end_time FROM exam_slots"
        " WHERE EXISTS (SELECT 1 FROM exams e WHERE e.slot_id = exam_slots.id)"
        " ORDER BY exam_date, start_time, end_time"
    ).fetchall()


def slot_exams(conn: sqlite3.Connection, slot_id: str) -> list[dict[str, Any]]:
    """返回某时间段内全部考场及其监考教师，供消息拼装。

    每个考场一行，teachers 按插入顺序（座位顺序）排列，并附 teacher_id/mobile/resolved。
    """
    exams = conn.execute(
        "SELECT e.id, c.code AS course_code, c.name AS course_name, r.name AS room_label"
        " FROM exams e"
        " JOIN courses c ON c.id = e.course_id"
        " JOIN rooms r ON r.id = e.room_id"
        " WHERE e.slot_id = ?"
        " ORDER BY e.rowid",
        (slot_id,),
    ).fetchall()

    rooms: list[dict[str, Any]] = []
    for e in exams:
        teachers = conn.execute(
            "SELECT i.teacher_id, i.teacher_name AS name, COALESCE(t.mobile, '') AS mobile,"
            " CASE WHEN i.teacher_id IS NULL THEN 0 ELSE 1 END AS resolved"
            " FROM exam_invigilators i"
            " LEFT JOIN teachers t ON t.id = i.teacher_id"
            " WHERE i.exam_id = ?"
            " ORDER BY i.rowid",
            (e["id"],),
        ).fetchall()
        rooms.append({
            "course_code": e["course_code"],
            "course_name": e["course_name"],
            "room_label": e["room_label"],
            "teachers": [dict(t) for t in teachers],
        })
    return rooms
