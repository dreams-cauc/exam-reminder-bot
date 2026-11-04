# -*- coding: utf-8 -*-
"""SQLite 连接与会话管理。

职责：
- 打开连接并设置外键、WAL、busy_timeout 等运行参数；
- 依据 PRAGMA user_version 判断是否需要初始化/升级表结构，避免每次连接重放 DDL；
- 提供统一的时间戳与主键生成工具。
"""

from __future__ import annotations

import sqlite3
import uuid
from collections.abc import Generator
from contextlib import contextmanager
from datetime import datetime
from pathlib import Path

from ..utils import config

# 结构版本号：结构变更时 +1，配合 PRAGMA user_version 做增量迁移
SCHEMA_VERSION = 2


def now_str() -> str:
    """Asia/Shanghai 当前时间，格式统一为 'YYYY-MM-DD HH:MM:SS'。"""
    return datetime.now(config.tz()).strftime("%Y-%m-%d %H:%M:%S")


def new_id() -> str:
    """生成 36 位 UUID 字符串，作为各表主键。

    SQLite 未内置 UUID 函数，故主键由应用层生成后写入。
    """
    return str(uuid.uuid4())


def _load_schema() -> str:
    """读取 schema.sql 内容：优先走包资源，失败时回退到文件路径。"""
    try:
        from importlib import resources

        return resources.files(__package__).joinpath("sql", "schema.sql").read_text(encoding="utf-8")
    except (ModuleNotFoundError, TypeError, FileNotFoundError):
        return (Path(__file__).resolve().parent / "sql" / "schema.sql").read_text(encoding="utf-8")


_schema_cache: str | None = None


def _schema() -> str:
    global _schema_cache
    if _schema_cache is None:
        _schema_cache = _load_schema()
    return _schema_cache


def _legacy_marker(conn: sqlite3.Connection) -> str:
    """识别旧版库结构，返回描述；新库/空库返回空串。"""
    tables = {row[0] for row in conn.execute("SELECT name FROM sqlite_master WHERE type = 'table'")}
    if "assignments" in tables:
        return "存在旧版表 assignments"
    if "teachers" in tables:
        cols = {row[1] for row in conn.execute("PRAGMA table_info(teachers)")}
        if "id" not in cols:
            return "teachers 表缺少 id 主键列"
    return ""


# v1 -> v2 迁移：teachers.role 增加 'developer'。SQLite 无法直接修改 CHECK 约束，
# 需重建表；此处重建语句需与 schema.sql 中 teachers 表定义保持一致。
_TEACHERS_V2_SQL = [
    """CREATE TABLE teachers_new (
        id          TEXT NOT NULL PRIMARY KEY CHECK (length(id) = 36),
        name        TEXT NOT NULL,
        dingtalk_id TEXT NOT NULL DEFAULT '',
        mobile      TEXT NOT NULL DEFAULT '',
        role        TEXT NOT NULL DEFAULT 'teacher' CHECK (role IN ('teacher', 'admin', 'developer')),
        is_active   INTEGER NOT NULL DEFAULT 1 CHECK (is_active IN (0, 1)),
        created_at  TEXT NOT NULL,
        updated_at  TEXT NOT NULL
    )""",
    "INSERT INTO teachers_new (id, name, dingtalk_id, mobile, role, is_active, created_at, updated_at)"
    " SELECT id, name, dingtalk_id, mobile, role, is_active, created_at, updated_at FROM teachers",
    "DROP TABLE teachers",
    "ALTER TABLE teachers_new RENAME TO teachers",
    "CREATE INDEX idx_teachers_name ON teachers (name)",
    "CREATE INDEX idx_teachers_role ON teachers (role)",
]


def _migrate_v1_to_v2(conn: sqlite3.Connection) -> None:
    """v1 -> v2：重建 teachers 表以放宽 role 域，保留原有数据与主键。"""
    conn.execute("PRAGMA foreign_keys = OFF")
    try:
        with conn:
            for stmt in _TEACHERS_V2_SQL:
                conn.execute(stmt)
    finally:
        conn.execute("PRAGMA foreign_keys = ON")


def _needs_teacher_migration(conn: sqlite3.Connection) -> bool:
    """teachers 表已存在但 role 域不含 'developer'（旧结构）时需重建。"""
    row = conn.execute(
        "SELECT sql FROM sqlite_master WHERE type = 'table' AND name = 'teachers'"
    ).fetchone()
    return row is not None and "developer" not in row[0]


# 历史版本中出现过的视图（含已废弃的 7 个与当前 3 个），精简列前统一清理。
_ALL_VIEWS = (
    "v_teachers",
    "v_courses",
    "v_rooms",
    "v_exam_slots",
    "v_invigilation_schedule",
    "v_reminder_status",
    "v_import_history",
    "v_exam_details",
    "v_invigilator_details",
    "v_due_reminders",
)


def _drop_columns(conn: sqlite3.Connection, table: str, columns: tuple[str, ...]) -> None:
    """删除表中已存在且不再入库的列（幂等，仅删存在的列）。"""
    existing = {row[1] for row in conn.execute(f"PRAGMA table_info({table})")}
    for column in columns:
        if column in existing:
            conn.execute(f"ALTER TABLE {table} DROP COLUMN {column}")


def _migrate_drop_unused_columns(conn: sqlite3.Connection) -> None:
    """移除不再入库的列：exams 精简人数/学院/来源，rooms 精简校区。

    SQLite 的 ALTER TABLE ... DROP COLUMN 要求被删列不被视图/触发器引用，
    故先清空全部视图；随后的 executescript 会重放 schema.sql 重建当前视图。
    """
    conn.execute("PRAGMA foreign_keys = OFF")
    try:
        for view in _ALL_VIEWS:
            conn.execute(f"DROP VIEW IF EXISTS {view}")
        _drop_columns(
            conn, "exams",
            ("enrolled", "required", "chief_college", "invigilator_college", "source_file"),
        )
        _drop_columns(conn, "rooms", ("campus",))
    finally:
        conn.execute("PRAGMA foreign_keys = ON")


def _ensure_schema(conn: sqlite3.Connection) -> None:
    """按版本号初始化/升级表结构；检测到不可兼容的旧库时给出明确指引。"""
    version = conn.execute("PRAGMA user_version").fetchone()[0]
    if version >= SCHEMA_VERSION:
        return

    legacy = _legacy_marker(conn)
    if legacy:
        raise RuntimeError(
            f"数据库结构与当前版本不兼容（{legacy}）。"
            "请先备份并删除旧库 data/monitor.db 后重新导入："
            "monitor iptjson --json user.json && monitor iptxlsx 监考表.xlsx"
        )

    conn.execute("PRAGMA journal_mode = WAL")
    if _needs_teacher_migration(conn):
        _migrate_v1_to_v2(conn)
    _migrate_drop_unused_columns(conn)
    conn.executescript(_schema())
    conn.execute(f"PRAGMA user_version = {SCHEMA_VERSION}")


def connect() -> sqlite3.Connection:
    """打开数据库连接；缺目录/缺结构时自动创建。"""
    config.ensure_dirs()
    conn = sqlite3.connect(config.DB_PATH, timeout=30)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    conn.execute("PRAGMA busy_timeout = 30000")
    _ensure_schema(conn)
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
