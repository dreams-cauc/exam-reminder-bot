# -*- coding: utf-8 -*-
"""提醒时刻计算、按考场整理正文、发送队列。

queue_reminders()  把 exams/assignments 展开成 reminders 行
process_due()      取到点且未过期的提醒，逐条发送并落状态
"""

from __future__ import annotations

import json
import logging
from datetime import datetime, timedelta

from . import config, db, dingtalk, messages
from .prompt import daybefore_invigilator_msg, t2h_invigilator_msg

logger = logging.getLogger(__name__)


def _dt(date_str: str, time_str: str) -> datetime:
    return datetime.strptime(f"{date_str} {time_str}", "%Y-%m-%d %H:%M")


def compute_schedule(exam_date: str, start_time: str) -> dict[str, str]:
    """两条提醒的计划触发时刻（上海时区）。"""
    start = _dt(exam_date, start_time)
    day_before = (start - timedelta(days=1)).replace(
        hour=config.DAY_BEFORE_HOUR,
        minute=config.DAY_BEFORE_MINUTE,
        second=0,
        microsecond=0,
    )
    t2h = start - timedelta(hours=config.T2H_OFFSET_HOURS)
    return {
        config.REMINDER_DAY_BEFORE: day_before.strftime("%Y-%m-%d %H:%M:%S"),
        config.REMINDER_T2H: t2h.strftime("%Y-%m-%d %H:%M:%S"),
    }


def _room_block(
    teachers: list[dict],
    *,
    template: str,
    course: str,
    time: str,
    location: str,
) -> tuple[str, list[str], list[str]] | None:
    """一个考场填一段正文，返回 (段落, 可@手机号, @失败姓名)；无可提醒教师返回 None。

    template: daybefore_invigilator_msg / t2h_invigilator_msg，按提醒阶段选。
    teachers: [{name, mobile, dingtalkid, resolved}, ...]，同名只保留首次出现。
    resolved=1 表示姓名在 teachers 名册中，只有他们会被提醒：
    有手机号填 @手机号（钉钉渲染成可点的 @），没有手机号填姓名且算 @ 失败，
    进异常清单推给管理员。名册外教师不提醒、不进异常清单，只出现在段末的
    监考教师名单里，让被提醒的教师知道同场监考还有谁。
    """
    seen: dict[str, dict] = {}
    for t in teachers:
        seen.setdefault(t["name"], t)

    mentions: list[str] = []
    at_mobiles: list[str] = []
    failed: list[str] = []
    for name, t in seen.items():
        if not t.get("resolved"):
            continue   # 名册外教师：只进监考名单，不提醒
        mobile = t.get("mobile") or ""
        if mobile:
            mentions.append(f"@{mobile}")
            at_mobiles.append(mobile)
        else:
            mentions.append(name)
            failed.append(name)

    if not mentions:
        return None   # 本考场没有库内教师，无需发段落

    block = messages.build_invigilator_message(
        template,
        at_teacher=" ".join(mentions),
        course=course,
        time=time,
        location=location,
        proctor_teacher=" ".join(seen),
    )
    return block, at_mobiles, failed


def _build_message(
    *,
    template: str,
    rooms: list[dict],
    exam_date: str,
    start_time: str,
    end_time: str,
    weekday_num: int,
) -> tuple[str, list[str], list[str]]:
    """按考场整理出一条完整消息，返回 (正文, 可@手机号, @失败姓名)。

    template: 提醒阶段模板（daybefore / t2h）。
    rooms: [{"campus", "building", "room", "course_name", "teachers": [...]}, ...]
    一个考场一段正文，全部段落合并成一条消息，结尾补 warm_tip；
    同一位教师排进多个考场时每段各自 @，atMobiles 去重。
    没有库内教师的考场整段跳过；所有考场都被跳过时正文为 ''。
    """
    time_text = messages.format_exam_time(exam_date, start_time, end_time, weekday_num)
    blocks: list[str] = []
    at_mobiles: list[str] = []
    failed: list[str] = []
    for room in rooms:
        built = _room_block(
            room["teachers"],
            template=template,
            course=room["course_name"],
            time=time_text,
            location=messages.room_label(
                room["campus"], room["building"], room["room"]
            ),
        )
        if built is None:
            continue
        block, room_mobiles, room_failed = built
        blocks.append(block)
        for mobile in room_mobiles:
            if mobile not in at_mobiles:
                at_mobiles.append(mobile)
        for name in room_failed:
            if name not in failed:
                failed.append(name)
    if not blocks:
        return "", at_mobiles, failed
    return messages.assemble_invigilator_message(blocks), at_mobiles, failed


def _exception_body(missing_names: list[str]) -> tuple[str, list[str]]:
    """库内教师 @ 失败时给管理员发提醒，返回 (正文, 管理员手机号)。"""
    with db.session() as conn:
        admins = conn.execute(
            "SELECT name, mobile FROM teachers WHERE role = 'admin' ORDER BY name"
        ).fetchall()
    if not admins:
        logger.warning("没有管理员（role='admin'），异常提醒无法发出：%s", missing_names)
        return "", []

    mobiles = [a["mobile"] for a in admins if a["mobile"]]
    # 管理员槽位内直接 @手机号（钉钉渲染成 @姓名），没手机号的退化为姓名；
    # @ 就地点名即可，结尾不另起一段单独的 @
    admin_text = " ".join(
        f"@{a['mobile']}" if a["mobile"] else a["name"] for a in admins
    )
    body = messages.build_exception_message(
        err_teacher=" ".join(missing_names),
        admin_teacher=admin_text,
    )
    return body, mobiles


def queue_reminders() -> dict[str, int]:
    """扫描库中考试，重建待发提醒队列。

    两条提醒各用各的模板：t2h 用「不足两小时」话术，day_before 用「明天」
    话术。考试已进入当天（含导入时已不足两小时）只建 t2h 行——到点即发，
    不会漏掉临近的监考，也不会再发误导性的「您明天的监考信息」。
    已发出（sent）的提醒不改动，保证重导入不重复打扰教师。
    返回 created / updated / kept 计数。
    """
    stats = {"created": 0, "updated": 0, "kept": 0}
    live_keys: set[str] = set()

    with db.session() as conn:
        today = db.now_str()[:10]
        for g in db.slot_groups(conn):
            rooms = [
                r for r in db.rooms_for_slot(
                    conn, g["exam_date"], g["start_time"], g["end_time"]
                )
                if r["teachers"]
            ]
            if not rooms:
                logger.warning("时间段无监考教师，跳过：%s %s-%s",
                               g["exam_date"], g["start_time"], g["end_time"])
                continue

            weekday_num = int(
                _dt(g["exam_date"], g["start_time"]).weekday()
            ) + 1
            # 两小时和两小时以内统一用 t2h 话术；@ 名单、异常清单与模板无关
            message, at_mobiles, missing = _build_message(
                template=t2h_invigilator_msg,
                rooms=rooms,
                exam_date=g["exam_date"],
                start_time=g["start_time"],
                end_time=g["end_time"],
                weekday_num=weekday_num,
            )
            if not message:
                logger.info("时间段内没有库内监考教师，跳过：%s %s-%s",
                            g["exam_date"], g["start_time"], g["end_time"])
                continue
            exception_message = ""
            if missing:
                exception_message, _ = _exception_body(missing)

            schedule = compute_schedule(g["exam_date"], g["start_time"])
            group_key = f"{g['exam_date']}|{g['start_time']}|{g['end_time']}"
            # 名单与消息一致：只记真正发出去的考场（含名单上的名册外教师）
            reminded = [
                r for r in rooms
                if any(x.get("resolved") for x in r["teachers"])
            ]
            names = list(dict.fromkeys(
                t["name"] for r in reminded for t in r["teachers"]
            ))
            base = {
                "course_code": "、".join(dict.fromkeys(
                    r["course_code"] for r in rooms if r["course_code"]
                )),
                "course_name": "、".join(dict.fromkeys(
                    r["course_name"] for r in rooms
                )),
                "exam_date": g["exam_date"],
                "start_time": g["start_time"],
                "end_time": g["end_time"],
                "names_json": json.dumps(names, ensure_ascii=False),
                "at_ids_json": json.dumps(at_mobiles, ensure_ascii=False),
                "missing_json": json.dumps(missing, ensure_ascii=False),
                "exception_message": exception_message,
            }

            rows = [(config.REMINDER_T2H, t2h_invigilator_msg, message)]
            if g["exam_date"] > today:
                # 前一天提醒：仅考试还没进入当天时才有「明天」话术
                day_msg, _, _ = _build_message(
                    template=daybefore_invigilator_msg,
                    rooms=rooms,
                    exam_date=g["exam_date"],
                    start_time=g["start_time"],
                    end_time=g["end_time"],
                    weekday_num=weekday_num,
                )
                rows.append((config.REMINDER_DAY_BEFORE, daybefore_invigilator_msg, day_msg))

            for reminder_type, _, row_message in rows:
                _, action = db.upsert_reminder(conn, {
                    "reminder_type": reminder_type,
                    "group_key": group_key,
                    "scheduled_at": schedule[reminder_type],
                    "message": row_message,
                    **base,
                })
                stats[action] += 1
                live_keys.add(group_key)

        # 考试进入当天后，作废还没发出的前一天提醒（t2h 行负责临近提醒）
        stale = db.cancel_stale_day_before(conn, today)
        if stale:
            logger.info("取消 %d 条过时的前一天提醒（考试已进入当天）", stale)

        # 清掉已无对应考试的待发提醒，避免发出过期内容
        cancelled = db.cancel_orphan_reminders(conn, live_keys)
        if cancelled:
            logger.info("取消 %d 条孤儿提醒（对应考试已不存在）", cancelled)

    logger.info("提醒队列重建完成：%s", stats)
    return stats


def process_due(*, dry_run: bool = False) -> int:
    """发送当前到点的提醒，返回成功条数。

    逐条原子占用（claim），进程重启也不会对同一条重复发送。
    一条消息合并了同一时间段的全部考场，不存在发到一半的半截状态。
    """
    now = db.now_str()
    with db.session() as conn:
        expired = db.expire_overdue(conn, now)
        if expired:
            logger.info("考试已结束，标记 %d 条提醒为 expired", expired)
        stale = db.cancel_stale_day_before(conn, now[:10])
        if stale:
            logger.info("取消 %d 条过时的前一天提醒（考试已进入当天）", stale)
        due = db.fetch_due(conn, now)

    if not due:
        return 0

    logger.info("到点提醒 %d 条", len(due))
    sent = 0
    for row in due:
        if dry_run:
            logger.info("[dry-run] %s %s\n%s",
                        row["reminder_type"], row["scheduled_at"], row["message"])
            sent += 1
            continue

        with db.session() as conn:
            if not db.claim_reminder(conn, row["id"]):
                continue   # 已被其它进程占走
        try:
            result = dingtalk.send_text(
                row["message"],
                at_mobiles=json.loads(row["at_ids_json"] or "[]"),
            )
        except dingtalk.DingTalkError as exc:
            with db.session() as conn:
                db.mark_failed(conn, row["id"], str(exc))
            logger.error("提醒发送异常 id=%s：%s", row["id"], exc)
            continue

        if not result.ok:
            with db.session() as conn:
                db.mark_failed(conn, row["id"], result.error_text)
            logger.error("提醒发送失败 id=%s：%s", row["id"], result.error_text)
            continue

        with db.session() as conn:
            db.mark_sent(conn, row["id"])
            # 库内教师 @ 失败时附带给管理员发一条异常提醒；失败只记录不回滚主提醒
            if row["exception_message"]:
                try:
                    admin_mobiles = [
                        r["mobile"] for r in conn.execute(
                            "SELECT mobile FROM teachers WHERE role = 'admin' AND mobile != ''"
                        )
                    ]
                    exc_result = dingtalk.send_text(
                        row["exception_message"], at_mobiles=admin_mobiles,
                    )
                    if not exc_result.ok:
                        db.set_last_error(conn, row["id"],
                                          f"异常提醒未发出: {exc_result.error_text}")
                except dingtalk.DingTalkError as exc:
                    db.set_last_error(conn, row["id"], f"异常提醒未发出: {exc}")
        sent += 1
        logger.info("已发送 %s id=%s", row["reminder_type"], row["id"])

    return sent
