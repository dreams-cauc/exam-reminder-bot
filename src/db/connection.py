# -*- coding: utf-8 -*-
"""SQLite 连接与会话管理。

职责：
- 打开连接并设置外键、WAL、busy_timeout 等运行参数；
- 依据 PRAGMA user_version 判断是否需要初始化/升级表结构，避免每次连接重放 DDL；
- 提供统一的时间戳与主键生成工具。

结构升级（用户端收到新版后首次启动自动完成，无需人工删库）：
- 首次运行：数据库为空，创建数据库，并写入 PRAGMA user_version = SCHEMA_VERSION。
- 后续升级：读到 user_version < SCHEMA_VERSION 时，按 MIGRATIONS 清单中
  「目标版本 > 当前版本」的顺序逐条执行迁移函数，再重放 schema.sql 补齐新增的
  表 / 视图 / 触发器，最后把 user_version 升到 SCHEMA_VERSION。
- 迁移函数只做「结构变更 + 老数据搬运」，必须幂等：内部用 PRAGMA table_info /
  sqlite_master 判断是否真的需要改动，从而既处理真正的老库，也容忍
  「表已最新但 user_version 漏写」的异常库，且不会重复破坏数据。

新增结构时按两条规则改代码：
1) 新增表 / 新增视图 / 新增触发器：只改 schema.sql（并重新生成 schema.py），
   再把 SCHEMA_VERSION 加 1 即可，无需写迁移函数——重放 schema.sql 会幂等创建。
2) 对「已有表」增 / 删 / 改列：除改 schema.sql 外，还要新增一个迁移函数挂到
   新的版本号上：新增列用 ALTER TABLE ... ADD COLUMN；删除列用 ALTER TABLE ...
   DROP COLUMN；改类型或改约束用「建新表 -> INSERT SELECT 拷贝老数据 -> DROP 旧表
   -> RENAME」重建（参见 _TEACHERS_V2_SQL 与 teachers 的 role 变更）。
"""

from __future__ import annotations

import sqlite3
import uuid
from collections.abc import Callable, Generator
from contextlib import contextmanager
from datetime import datetime

from .schema import SCHEMA_SQL
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


def _needs_teacher_migration(conn: sqlite3.Connection) -> bool:
    """teachers 表已存在但 role 域不含 'developer'（旧结构）时需重建。"""
    row = conn.execute(
        "SELECT sql FROM sqlite_master WHERE type = 'table' AND name = 'teachers'"
    ).fetchone()
    return row is not None and "developer" not in row[0]


def _rebuild_teachers_for_developer_role(conn: sqlite3.Connection) -> None:
    """重建 teachers 表以放宽 role 域，保留原有数据与主键。"""
    conn.execute("PRAGMA foreign_keys = OFF")
    try:
        with conn:
            for stmt in _TEACHERS_V2_SQL:
                conn.execute(stmt)
    finally:
        conn.execute("PRAGMA foreign_keys = ON")


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


def _migrate_v1_to_v2(conn: sqlite3.Connection) -> None:
    """v1 -> v2 的两处结构变更：teachers 增加 'developer' 角色、精简未入库列。

    两者均为幂等操作，不会破坏老数据。
    """
    if _needs_teacher_migration(conn):
        _rebuild_teachers_for_developer_role(conn)
    _migrate_drop_unused_columns(conn)


# 结构迁移清单：每个条目 (目标版本, 迁移函数) 表示把数据库从「目标版本 - 1」
# 升级到「目标版本」。_ensure_schema 按版本号升序执行尚未应用的条目。
# 仅「已有表」的增/删/改列需要在此登记；新增表/视图/触发器只改 schema.sql 即可。
MIGRATIONS: list[tuple[int, Callable[[sqlite3.Connection], None]]] = [
    (2, _migrate_v1_to_v2),
]


def _is_fresh_db(conn: sqlite3.Connection) -> bool:
    """数据库尚无任何业务表（sqlite 内置表除外）时为全新库。"""
    count = conn.execute(
        "SELECT COUNT(*) FROM sqlite_master WHERE type = 'table' AND name NOT LIKE 'sqlite_%'"
    ).fetchone()[0]
    return count == 0


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

    if _is_fresh_db(conn):
        # 全新库：直接按最新结构建表，无需跑迁移。
        conn.executescript(SCHEMA_SQL)
    else:
        # 已有数据：逐条执行尚未应用的增量迁移，再重放 schema.sql 补齐
        # 新增表/视图/触发器（DDL 幂等，不会重建已存在的表）。
        for target, migrate in MIGRATIONS:
            if version < target:
                migrate(conn)
                conn.execute(f"PRAGMA user_version = {target}")
                version = target
        conn.executescript(SCHEMA_SQL)

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
