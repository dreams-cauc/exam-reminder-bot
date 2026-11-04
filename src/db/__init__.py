# -*- coding: utf-8 -*-
"""数据库访问层：连接管理、实体 CRUD 与提醒队列状态机。

调用方（importers / scheduler 等）统一通过本包暴露的接口访问，
不直接接触底层 SQLite 连接与 SQL 细节。
"""

from .connection import SCHEMA_VERSION, connect, session, now_str, new_id
from .teachers import teacher_name_index, resolve_teacher_id, upsert_teacher, list_admins
from .reference import get_or_create_course, get_or_create_room, get_or_create_slot
from .exams import upsert_exam, set_invigilators, list_slots, slot_exams
from .reminders import (
    STATUS_PENDING,
    STATUS_SENDING,
    STATUS_SENT,
    STATUS_FAILED,
    STATUS_EXPIRED,
    STATUS_CANCELLED,
    upsert_reminder,
    cancel_orphan_reminders,
    cancel_stale_day_before,
    fetch_due,
    expire_overdue,
    count_due_rooms,
    reminder_recipients,
    claim_reminder,
    mark_sent,
    mark_failed,
    set_last_error,
)
from .imports import create_batch, add_error, finalize_batch

__all__ = [
    "SCHEMA_VERSION",
    "connect",
    "session",
    "now_str",
    "new_id",
    "teacher_name_index",
    "resolve_teacher_id",
    "upsert_teacher",
    "list_admins",
    "get_or_create_course",
    "get_or_create_room",
    "get_or_create_slot",
    "upsert_exam",
    "set_invigilators",
    "list_slots",
    "slot_exams",
    "STATUS_PENDING",
    "STATUS_SENDING",
    "STATUS_SENT",
    "STATUS_FAILED",
    "STATUS_EXPIRED",
    "STATUS_CANCELLED",
    "upsert_reminder",
    "cancel_orphan_reminders",
    "cancel_stale_day_before",
    "fetch_due",
    "expire_overdue",
    "count_due_rooms",
    "reminder_recipients",
    "claim_reminder",
    "mark_sent",
    "mark_failed",
    "set_last_error",
    "create_batch",
    "add_error",
    "finalize_batch",
]
