# -*- coding: utf-8 -*-
"""参考数据实体的读取或创建：课程、考场、考试时间段。"""

from __future__ import annotations

import sqlite3

from .connection import new_id, now_str


def get_or_create_course(conn: sqlite3.Connection, *, code: str, name: str) -> str:
    """按 (课程号, 课程名) 查找课程，不存在则创建，返回 id。"""
    row = conn.execute("SELECT id FROM courses WHERE code = ? AND name = ?", (code, name)).fetchone()
    if row:
        return row["id"]
    cid = new_id()
    ts = now_str()
    conn.execute(
        "INSERT INTO courses (id, code, name, created_at, updated_at) VALUES (?, ?, ?, ?, ?)",
        (cid, code, name, ts, ts),
    )
    return cid


def get_or_create_room(
    conn: sqlite3.Connection,
    *,
    name: str,
    building: str = "",
    room_no: str = "",
) -> str:
    """按规范化考场名称查找考场，不存在则创建，返回 id。"""
    row = conn.execute("SELECT id FROM rooms WHERE name = ?", (name,)).fetchone()
    if row:
        return row["id"]
    rid = new_id()
    ts = now_str()
    conn.execute(
        "INSERT INTO rooms (id, name, building, room_no, created_at, updated_at)"
        " VALUES (?, ?, ?, ?, ?, ?)",
        (rid, name, building, room_no, ts, ts),
    )
    return rid


def get_or_create_slot(
    conn: sqlite3.Connection,
    *,
    exam_date: str,
    start_time: str,
    end_time: str,
) -> str:
    """按 (日期, 起, 止) 查找时间段，不存在则创建，返回 id。"""
    row = conn.execute(
        "SELECT id FROM exam_slots WHERE exam_date = ? AND start_time = ? AND end_time = ?",
        (exam_date, start_time, end_time),
    ).fetchone()
    if row:
        return row["id"]
    sid = new_id()
    ts = now_str()
    conn.execute(
        "INSERT INTO exam_slots (id, exam_date, start_time, end_time, created_at, updated_at)"
        " VALUES (?, ?, ?, ?, ?, ?)",
        (sid, exam_date, start_time, end_time, ts, ts),
    )
    return sid
