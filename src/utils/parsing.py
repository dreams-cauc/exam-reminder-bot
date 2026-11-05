# -*- coding: utf-8 -*-
"""字段解析：考试时间字符串、课程字段、姓名归一化。

考试时间示例：`第8周,周5,(2026-10-23),13:30-15:30`
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import date, time


class ParseError(ValueError):
    """字段无法解析。调用方应把该行记入拒收清单，不得静默丢弃。"""


# 全角标点、常见变体统一折算成半角，便于正则匹配
_TRANS = str.maketrans({
    "，": ",", "、": ",", "；": ";",
    "（": "(", "）": ")",
    "：": ":", "－": "-", "—": "-", "–": "-", "～": "-", "〜": "-",
    "　": " ", "／": "/",
})

# 第8周,周5,(2026-10-23),13:30-15:30
_TIME_RE = re.compile(
    r"第\s*(?P<week>\d+)\s*周\s*,\s*"
    r"周\s*(?P<weekday>\d+|[一二三四五六日天])\s*,\s*"
    r"\(\s*(?P<y>\d{4})\s*[-/.年]\s*(?P<m>\d{1,2})\s*[-/.月]\s*(?P<d>\d{1,2})\s*日?\s*\)\s*,\s*"
    r"(?P<sh>\d{1,2})\s*:\s*(?P<sm>\d{2})\s*-\s*"
    r"(?P<eh>\d{1,2})\s*:\s*(?P<em>\d{2})"
)

# 中文星期 -> 数字（周1=周一 ... 周7=周日）
_WEEK_CN = {"一": 1, "二": 2, "三": 3, "四": 4, "五": 5, "六": 6, "日": 7, "天": 7}


@dataclass(frozen=True)
class ExamTime:
    week_num: int
    weekday_num: int          # 1=周一 ... 7=周日
    date: date
    start: time
    end: time

    @property
    def exam_date(self) -> str:
        return self.date.strftime("%Y-%m-%d")

    @property
    def start_time(self) -> str:
        return self.start.strftime("%H:%M")

    @property
    def end_time(self) -> str:
        return self.end.strftime("%H:%M")


def normalize_text(value: object) -> str:
    """单元格取值归一化成半角空白分隔的字符串。"""
    if value is None:
        return ""
    text = str(value).translate(_TRANS)
    return re.sub(r"\s+", " ", text).strip()


def normalize_name(value: object) -> str:
    """姓名归一化：去空白、去中间点之外的标点干扰。"""
    text = normalize_text(value)
    return re.sub(r"\s+", "", text)


def split_course(raw: str) -> tuple[str, str]:
    """`02401503/概率论与数理统计(I)` -> (`02401503`, `概率论与数理统计(I)`)。

    无分隔符时课程号置空，整串作为课程名。
    """
    text = normalize_text(raw)
    if not text:
        raise ParseError("课程字段为空")
    for sep in ("/",):
        if sep in text:
            code, name = text.split(sep, 1)
            code, name = code.strip(), name.strip()
            if not name:
                raise ParseError(f"课程名为空：{raw!r}")
            return code, name
    return "", text


def parse_exam_time(raw: object) -> ExamTime:
    """解析考试时间字符串。

    解析失败抛 ParseError；星期与日期不一致同样抛 ParseError。
    """
    text = normalize_text(raw)
    if not text:
        raise ParseError("考试时间字段为空")

    m = _TIME_RE.search(text)
    if not m:
        raise ParseError(f"考试时间格式无法识别：{raw!r}")

    week_num = int(m.group("week"))
    wk_raw = m.group("weekday")
    weekday_num = int(wk_raw) if wk_raw.isdigit() else _WEEK_CN[wk_raw]

    try:
        exam_date = date(int(m.group("y")), int(m.group("m")), int(m.group("d")))
    except ValueError as exc:
        raise ParseError(f"考试日期非法：{raw!r}（{exc}）") from exc

    sh, sm = int(m.group("sh")), int(m.group("sm"))
    eh, em = int(m.group("eh")), int(m.group("em"))
    if not (0 <= sh <= 23 and 0 <= eh <= 23 and 0 <= sm <= 59 and 0 <= em <= 59):
        raise ParseError(f"考试时刻非法：{raw!r}")
    start, end = time(sh, sm), time(eh, em)
    if end <= start:
        raise ParseError(f"结束时间不晚于开始时间：{raw!r}")

    # Python weekday(): 周一=0；中文周N：周一=1
    if exam_date.weekday() + 1 != weekday_num:
        raise ParseError(
            f"星期与日期不一致：{raw!r}（{exam_date} 实为周{exam_date.weekday() + 1}）"
        )

    return ExamTime(
        week_num=week_num, weekday_num=weekday_num,
        date=exam_date, start=start, end=end,
    )


def parse_int(raw: object) -> int | None:
    """人数等数字字段，空串返回 None，非数字返回 None 不阻断导入。"""
    text = normalize_text(raw)
    if not text:
        return None
    try:
        return int(float(text))
    except ValueError:
        return None


_MOBILE_RE = re.compile(r"^1\d{10}$")

# 角色别名 -> 规范角色。大小写由调用处统一转小写后再匹配。
_ROLE_ALIASES = {
    "admin": "admin",
    "administrator": "admin",
    "管理员": "admin",
    "管理": "admin",
    "teacher": "teacher",
    "教师": "teacher",
    "老师": "teacher",
    "developer": "developer",
    "dev": "developer",
    "开发者": "developer",
    "开发": "developer",
}


def normalize_role(value: object) -> str:
    """角色归一化到 'teacher' / 'admin' / 'developer'。

    兼容中英文别名与大小写；无法识别时抛 ParseError 给出清晰提示，
    避免把脏角色写进数据库触发 CHECK 约束。
    """
    text = normalize_text(value).lower()
    if text in _ROLE_ALIASES:
        return _ROLE_ALIASES[text]
    raise ParseError(f"无法识别的角色：{value!r}（可选：teacher / admin / developer）")


def normalize_mobile(value: object) -> str:
    """手机号归一化成 11 位数字。

    空值返回 ''（表示未登记，不参与 @）；格式不对同样返回 ''，
    让调用方当作「未登记」处理，避免把脏号塞进 atMobiles 导致整条消息发送失败。
    """
    text = re.sub(r"[\s\-()]", "", normalize_text(value))
    if not text:
        return ""
    return text if _MOBILE_RE.match(text) else ""
