# -*- coding: utf-8 -*-
"""教师名册读写。"""

from __future__ import annotations

import logging
import sqlite3

from .connection import new_id, now_str

logger = logging.getLogger(__name__)


def teacher_name_index(conn: sqlite3.Connection) -> dict[str, list[str]]:
    """返回 姓名 -> [教师 id] 索引，供 Excel 导入时按姓名批量解析。"""
    index: dict[str, list[str]] = {}
    for row in conn.execute(
        "SELECT id, name FROM teachers WHERE is_active = 1 ORDER BY created_at, id"
    ):
        index.setdefault(row["name"], []).append(row["id"])
    return index


def resolve_teacher_id(conn: sqlite3.Connection, name: str) -> str | None:
    """按姓名解析名册中的教师：唯一命中返回 id，未命中或多义返回 None。"""
    rows = conn.execute(
        "SELECT id FROM teachers WHERE name = ? AND is_active = 1 ORDER BY created_at, id",
        (name,),
    ).fetchall()
    return rows[0]["id"] if len(rows) == 1 else None


_VALID_ROLES = {"teacher", "admin", "developer"}


def _find_teacher(
    conn: sqlite3.Connection, dingtalk_id: str, mobile: str
) -> tuple[str, str] | None:
    """按钉钉账号、手机号依次定位同一教师，返回 (id, name) 或 None。

    姓名允许重名，不作为判定依据；停用教师也会被命中，便于重新导入时复用主键。
    """
    if dingtalk_id:
        row = conn.execute(
            "SELECT id, name FROM teachers WHERE dingtalk_id = ? ORDER BY created_at, id",
            (dingtalk_id,),
        ).fetchone()
        if row:
            return row["id"], row["name"]
    if mobile:
        row = conn.execute(
            "SELECT id, name FROM teachers WHERE mobile = ? ORDER BY created_at, id",
            (mobile,),
        ).fetchone()
        if row:
            return row["id"], row["name"]
    return None


def upsert_teacher(
    conn: sqlite3.Connection,
    *,
    name: str,
    dingtalk_id: str = "",
    mobile: str = "",
    role: str = "teacher",
) -> str:
    """写入或更新一名教师，返回其 id。

    教师是否同一人以钉钉账号 / 手机号判定（姓名允许重名，不作判定依据）：
    命中即复用主键并更新信息，未命中才新增，保证同一教师不重复入库。
    """
    if role not in _VALID_ROLES:
        raise ValueError(f"非法角色 {role!r}，可选：teacher / admin / developer")

    ts = now_str()
    found = _find_teacher(conn, dingtalk_id, mobile)
    if found is not None:
        tid, old_name = found
        if old_name != name:
            logger.warning(
                "教师身份复用：%s -> %s（按钉钉账号/手机号判定为同一人）", old_name, name
            )
        conn.execute(
            "UPDATE teachers SET name = ?, dingtalk_id = ?, mobile = ?, role = ?,"
            " is_active = 1, updated_at = ? WHERE id = ?",
            (name, dingtalk_id, mobile, role, ts, tid),
        )
        return tid

    tid = new_id()
    conn.execute(
        "INSERT INTO teachers (id, name, dingtalk_id, mobile, role, is_active, created_at, updated_at)"
        " VALUES (?, ?, ?, ?, ?, 1, ?, ?)",
        (tid, name, dingtalk_id, mobile, role, ts, ts),
    )
    return tid


def list_admins(conn: sqlite3.Connection) -> list[sqlite3.Row]:
    """返回异常通知接收人（role in admin/developer），供异常通知使用。"""
    return conn.execute(
        "SELECT id, name, mobile FROM teachers"
        " WHERE role IN ('admin', 'developer') AND is_active = 1 ORDER BY name"
    ).fetchall()
