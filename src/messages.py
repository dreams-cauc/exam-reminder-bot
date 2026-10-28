# -*- coding: utf-8 -*-
"""
按名称填充：
一个考场填一段正文，同一时间段的多个考场段落合并成一条消息，
结尾补 warm_tip。
"""

from __future__ import annotations

import re

from .prompt import error_feedback_msg, helloteacher, warm_tip

WEEKDAY_CN = ("周一", "周二", "周三", "周四", "周五", "周六", "周日")

_PLACEHOLDER_RE = re.compile(r"\{(\w+)\}")


def fill(template: str, **values: object) -> str:
    """按名称填充 {xxx} 占位符。

    手工替换而非 str.format：教师姓名等外部数据里的花括号不能破坏模板，
    一遍替换也不会把填进去的值二次当占位符解析。
    """
    def repl(match: re.Match) -> str:
        key = match.group(1)
        if key not in values:
            raise ValueError(f"模板占位符 {{{key}}} 没有对应的值")
        return str(values[key])
    return _PLACEHOLDER_RE.sub(repl, template)


def format_exam_time(exam_date: str, start_time: str, end_time: str, weekday_num: int) -> str:
    """`2026-10-23(周五) 13:30-15:30`"""
    wd = WEEKDAY_CN[weekday_num - 1] if 1 <= weekday_num <= 7 else ""
    return f"{exam_date}({wd}) {start_time}-{end_time}"


def room_label(campus: str, building: str, room: str) -> str:
    """考场显示串：`南教3-104`。

    教室列本身已含楼号（如 `南教3-104`）时原样返回，避免重复拼接。
    """
    if not room:
        return f"{campus}{building}"
    head = f"{campus}{building}"
    if head and not room.startswith(head):
        return f"{head}-{room}"
    return room


def build_invigilator_message(
    template: str,
    *,
    at_teacher: str,
    course: str,
    time: str,
    location: str,
    proctor_teacher: str,
) -> str:
    """单个考场的监考提醒正文（含结尾分隔线与空行）。

    template        提醒阶段模板：daybefore_invigilator_msg / t2h_invigilator_msg
    at_teacher      收件人槽位：可 @ 的填 @手机号，其余填姓名，空格分隔
    course          监考科目
    time            监考时间
    location        监考教室：本考场
    proctor_teacher 监考教师槽位：本考场全部教师姓名，空格分隔
    """
    return fill(
        template,
        at_teacher=at_teacher,
        course=course,
        time=time,
        location=location,
        proctor_teacher=proctor_teacher,
    )


def assemble_invigilator_message(blocks: list[str]) -> str:
    """各考场段落合并成一条待发消息，结尾追加 warm_tip。"""
    return "".join(blocks) + warm_tip


def build_hello_message() -> str:
    """机器人打招呼正文。

    开头 {at_all} 占位填 @所有人；发送时需带 is_at_all=True，
    钉钉才会真正 @ 全体成员。
    """
    return fill(helloteacher, hi_all="Hi~")


def build_exception_message(*, err_teacher: str, admin_teacher: str) -> str:
    """库内教师 @ 失败时的管理员提醒正文。

    err_teacher     @ 失败的教师姓名，空格分隔
    admin_teacher   管理员槽位内填 @手机号（钉钉渲染成 @姓名）就地点名，
                    结尾不再追加单独的 @
    """
    return fill(error_feedback_msg, err_teacher=err_teacher, admin_teacher=admin_teacher)
