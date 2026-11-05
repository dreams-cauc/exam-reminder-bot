# -*- coding: utf-8 -*-
"""导入批次与拒收明细的写入。"""

from __future__ import annotations

import sqlite3

from .connection import new_id, now_str


def create_batch(conn: sqlite3.Connection, *, source_type: str, source_file: str) -> str:
    """创建一条导入批次记录，返回批次 id。"""
    bid = new_id()
    conn.execute(
        "INSERT INTO import_batches (id, source_type, source_file, rows_seen, rows_imported, rows_rejected, created_at)"
        " VALUES (?, ?, ?, 0, 0, 0, ?)",
        (bid, source_type, source_file, now_str()),
    )
    return bid


def add_error(conn: sqlite3.Connection, batch_id: str, row_no: int, reason: str) -> None:
    """记录一条被拒收的行及原因。"""
    conn.execute(
        "INSERT INTO import_errors (id, batch_id, row_no, reason, created_at) VALUES (?, ?, ?, ?, ?)",
        (new_id(), batch_id, row_no, reason, now_str()),
    )


def finalize_batch(
    conn: sqlite3.Connection,
    batch_id: str,
    *,
    rows_seen: int,
    rows_imported: int,
    rows_rejected: int,
) -> None:
    """回填批次统计。"""
    conn.execute(
        "UPDATE import_batches SET rows_seen = ?, rows_imported = ?, rows_rejected = ? WHERE id = ?",
        (rows_seen, rows_imported, rows_rejected, batch_id),
    )
