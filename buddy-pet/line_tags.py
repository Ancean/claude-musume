"""闲聊区台词的条件：写在句子开头的标记，比如“【见面】今天也顺利登录了”。

没有标记的句子随机说。见面类的句子只在她刚出来、被叫醒或者从隐藏里叫出来时说，同一句每天最多一次；
时段标记让随机的句子只在那个时段出现。桌宠（pet.pyw）和台词管理窗口（lines_editor.py）都用这里的规则。
"""

from __future__ import annotations

from datetime import datetime, timedelta

MEET = "见面"
FIRST = "今天第一次"
# 时段标记和 pet.pyw 里问候台词的时段一致。
PERIODS = {"早上": "morning", "白天": "day", "傍晚": "evening", "深夜": "night"}
# 台词管理窗口“条件”菜单里的选项：（标记, 说明）。
CONDITIONS = (
    (MEET, "见面时说：她刚出来、被叫醒、从隐藏里叫出来时，约一半机会说；同一句每天最多一次"),
    (FIRST, "每天第一次见面时说，优先于“见面”；每天 5 点重新算"),
    ("早上", "随机说，只在 5 点到 10 点"),
    ("白天", "随机说，只在 10 点到 17 点"),
    ("傍晚", "随机说，只在 17 点到 23 点"),
    ("深夜", "随机说，只在 23 点到第二天 5 点"),
)
# 只有这几类台词能加条件。
TAGGED_KINDS = ("chat",)
# “每天”从这个钟点算起，熬夜过了零点还算前一天。
DAY_START = 5


def period_of(hour: int) -> str:
    return "night" if hour >= 23 or hour < 5 else "morning" if hour < 10 else "evening" if hour >= 17 else "day"


def day_key(now: datetime) -> str:
    return (now - timedelta(hours=DAY_START)).date().isoformat()


def split(line: str) -> tuple[list[str], str]:
    """把句子开头的【条件】（也认 [条件]）拆出来：（条件列表, 剩下的台词）。"""
    tags, rest = [], line.lstrip()
    while rest[:1] in ("【", "["):
        end = rest.find("】" if rest[0] == "【" else "]")
        if end < 0:
            break
        tags.append(rest[1:end].strip())
        rest = rest[end + 1 :].lstrip()
    return tags, rest


def join(tags: list[str], text: str) -> str:
    """条件按“见面类在前、时段在后”的顺序写回句子开头。"""
    order = [MEET, FIRST, *PERIODS]
    return "".join(f"【{t}】" for t in sorted(tags, key=order.index)) + text


def problem(tags: list[str], kind: str | None) -> str | None:
    if not tags:
        return None
    if kind not in TAGGED_KINDS:
        return "【条件】只在“闲聊区”里有用"
    for t in tags:
        if t != MEET and t != FIRST and t not in PERIODS:
            return f"【{t}】不是可用的条件，可用的有：" + "、".join(name for name, _ in CONDITIONS)
    if MEET in tags and FIRST in tags:
        return "【见面】和【今天第一次】只能选一个"
    if sum(t in PERIODS for t in tags) > 1:
        return "时段只能选一个"
    return None


def fits(tags: list[str], period: str) -> bool:
    """时段条件符不符合现在。"""
    want = [PERIODS[t] for t in tags if t in PERIODS]
    return not want or period in want
