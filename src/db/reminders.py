# -*- coding: utf-8 -*-
"""提醒队列：状态机与收件人快照。"""

from __future__ import annotations

import sqlite3
from typing import Any

from .connection import new_id, now_str

STATUS_PENDING = "pending"
STATUS_SENDING = "sending"
STATUS_SENT = "sent"
STATUS_FAILED = "failed"
STATUS_EXPIRED = "expired"
STATUS_CANCELLED = "cancelled"


def _replace_recipients(
    conn: sqlite3.Connection,
    reminder_id: str,
    recipients: list[dict[str, Any]],
) -> None:
    conn.execute("DELETE FROM reminder_recipients WHERE reminder_id = ?", (reminder_id,))
    ts = now_str()
    conn.executemany(
        "INSERT INTO reminder_recipients (id, reminder_id, teacher_id, teacher_name, mobile, created_at)"
        " VALUES (?, ?, ?, ?, ?, ?)",
        [(new_id(), reminder_id, r["teacher_id"], r["teacher_name"], r["mobile"], ts) for r in recipients],
    )


def upsert_reminder(
    conn: sqlite3.Connection,
    *,
    reminder_type: str,
    slot_id: str,
    scheduled_at: str,
    message: str,
    exception_message: str,
    recipients: list[dict[str, Any]],
) -> tuple[str, str]:
    """写入/更新一条提醒及收件人。已发出/发送中的记录不再改写。

    返回 (reminder_id, action)，action 为 created / updated / kept。
    """
    existing = conn.execute(
        "SELECT id, status FROM reminders WHERE reminder_type = ? AND slot_id = ?",
        (reminder_type, slot_id),
    ).fetchone()
    ts = now_str()

    if existing is None:
        rid = new_id()
        conn.execute(
            "INSERT INTO reminders (id, reminder_type, slot_id, scheduled_at, message,"
            " exception_message, status, attempts, last_error, created_at, updated_at)"
            " VALUES (?, ?, ?, ?, ?, ?, ?, 0, '', ?, ?)",
            (rid, reminder_type, slot_id, scheduled_at, message, exception_message, STATUS_PENDING, ts, ts),
        )
        _replace_recipients(conn, rid, recipients)
        return rid, "created"

    rid = existing["id"]
    if existing["status"] in (STATUS_SENT, STATUS_SENDING):
        return rid, "kept"

    conn.execute(
        "UPDATE reminders SET scheduled_at = ?, message = ?, exception_message = ?,"
        " status = ?, updated_at = ? WHERE id = ?",
        (scheduled_at, message, exception_message, STATUS_PENDING, ts, rid),
    )
    _replace_recipients(conn, rid, recipients)
    return rid, "updated"


def cancel_orphan_reminders(conn: sqlite3.Connection, keep_slot_ids: set[str]) -> int:
    """把已无对应考试的待发提醒置为 cancelled，避免发出过期内容。"""
    rows = conn.execute(
        "SELECT id, slot_id FROM reminders WHERE status IN (?, ?)",
        (STATUS_PENDING, STATUS_FAILED),
    ).fetchall()
    ts = now_str()
    cancelled = 0
    for row in rows:
        if row["slot_id"] in keep_slot_ids:
            continue
        conn.execute(
            "UPDATE reminders SET status = ?, updated_at = ? WHERE id = ?",
            (STATUS_CANCELLED, ts, row["id"]),
        )
        cancelled += 1
    return cancelled


def cancel_stale_day_before(conn: sqlite3.Connection, today: str) -> int:
    """考试已进入当天后，作废尚未发出的前一天提醒。"""
    cur = conn.execute(
        "UPDATE reminders SET status = ?, updated_at = ?"
        " WHERE reminder_type = ? AND status IN (?, ?)"
        " AND slot_id IN (SELECT id FROM exam_slots WHERE exam_date <= ?)",
        (STATUS_CANCELLED, now_str(), "day_before", STATUS_PENDING, STATUS_FAILED, today),
    )
    return cur.rowcount


def fetch_due(conn: sqlite3.Connection, now: str) -> list[sqlite3.Row]:
    """取出到点且考试尚未结束的待发提醒。"""
    return conn.execute(
        "SELECT r.* FROM reminders r"
        " JOIN exam_slots s ON s.id = r.slot_id"
        " WHERE r.status = ? AND r.scheduled_at <= ? AND (s.exam_date || ' ' || s.end_time) > ?"
        " ORDER BY r.scheduled_at, r.rowid",
        (STATUS_PENDING, now, now),
    ).fetchall()


def expire_overdue(conn: sqlite3.Connection, now: str) -> int:
    """考试已结束仍未发出的提醒置为 expired。"""
    cur = conn.execute(
        "UPDATE reminders SET status = ?, updated_at = ?"
        " WHERE status = ? AND slot_id IN ("
        " SELECT id FROM exam_slots WHERE (exam_date || ' ' || end_time) <= ?)",
        (STATUS_EXPIRED, now, STATUS_PENDING, now),
    )
    return cur.rowcount


def reminder_recipients(conn: sqlite3.Connection, reminder_id: str) -> list[sqlite3.Row]:
    """返回一条提醒的收件人（按插入顺序）。"""
    return conn.execute(
        "SELECT teacher_id, teacher_name, mobile FROM reminder_recipients"
        " WHERE reminder_id = ? ORDER BY rowid",
        (reminder_id,),
    ).fetchall()


def claim_reminder(conn: sqlite3.Connection, reminder_id: str) -> bool:
    """原子占用一条待发提醒，防止重复发送。"""
    cur = conn.execute(
        "UPDATE reminders SET status = ?, attempts = attempts + 1, updated_at = ?"
        " WHERE id = ? AND status = ?",
        (STATUS_SENDING, now_str(), reminder_id, STATUS_PENDING),
    )
    return cur.rowcount == 1


def mark_sent(conn: sqlite3.Connection, reminder_id: str) -> None:
    ts = now_str()
    conn.execute(
        "UPDATE reminders SET status = ?, sent_at = ?, last_error = '', updated_at = ? WHERE id = ?",
        (STATUS_SENT, ts, ts, reminder_id),
    )


def mark_failed(conn: sqlite3.Connection, reminder_id: str, error: str) -> None:
    conn.execute(
        "UPDATE reminders SET status = ?, last_error = ?, updated_at = ? WHERE id = ?",
        (STATUS_FAILED, error[:500], now_str(), reminder_id),
    )


def set_last_error(conn: sqlite3.Connection, reminder_id: str, error: str) -> None:
    """主消息已发出、附属通知失败时只记录，不回滚状态。"""
    conn.execute(
        "UPDATE reminders SET last_error = ?, updated_at = ? WHERE id = ?",
        (error[:500], now_str(), reminder_id),
    )
