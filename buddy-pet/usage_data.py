"""额度数据：读 Claude 桌面端的额度历史和 Claude Code 插件写的会话文件，合并成桌宠显示的一份。

两个来源，谁的读数新用谁：
- %APPDATA%\\Claude\\plan-usage-history.json：桌面端约每 15 分钟写一次 5 小时与 7 天额度的已用百分比，
  里面没有重置时间，重置时间由这份历史推算，显示时标“约”。
- %USERPROFILE%\\.claude\\buddy-pet\\live\\<会话 id>.json：插件 claude-buddy-bridge 写的，每个 Claude Code
  会话一个文件，有准确的重置时间、上下文 token、本会话花费和任务的起止时间，额度每变动一个百分点更新一次。
两边都不涉及登录凭据。只用标准库。
"""

from __future__ import annotations

import json
import math
import os
from dataclasses import dataclass, field, replace
from datetime import datetime
from pathlib import Path

FIVE_HOURS = 5 * 3600
WEEK = 7 * 86400
# 相邻两条记录间隔超过这么久，就推算不准窗口从哪一刻开始。
MAX_GAP = 45 * 60
# 最新读数比这更旧，说明桌面端和 Claude Code 都没在更新。
STALE_AFTER = 40 * 60
# 插件每分钟写一次心跳；会话文件超过这么久没更新，就当这个会话已经不在了。
LIVE_FRESH = 180
# 会话文件超过这么久没更新就删掉，只删插件目录里的 .json。
LIVE_KEEP = 3 * 86400
# 消耗速度：（看最近多少秒, 数据至少跨多少秒才预测, 每小时少于这个百分比就当没在用）。
PACE = {"five": (3600, 15 * 60, 0.5), "seven": (86400, 3 * 3600, 0.05)}
SPANS = {"five": FIVE_HOURS, "seven": WEEK}
WEEKDAYS = "一二三四五六日"


@dataclass
class Usage:
    five: float | None = None  # 5 小时额度已用百分比
    seven: float | None = None  # 7 天额度已用百分比
    at: float | None = None  # 最新读数的时间（秒）
    five_reset: float | None = None  # 5 小时额度重置时刻（秒）
    seven_reset: float | None = None  # 7 天额度重置时刻（秒）
    missing: bool = False  # 两个来源都找不到
    five_exact: bool = False  # 重置时间来自插件，是准确的；否则是推算的
    seven_exact: bool = False
    five_rate: float | None = None  # 最近平均每小时用掉的百分比
    seven_rate: float | None = None
    tokens: int | None = None  # 最近活动的 Claude Code 会话：上下文里的 token 数
    window: int | None = None  # 这个会话的上下文窗口大小
    cost: float | None = None  # 这个会话到现在的花费（美元）

    def known(self) -> bool:
        return self.five is not None or self.seven is not None

    def stale(self, now: float) -> bool:
        return self.at is not None and now - self.at > STALE_AFTER

    def outlook(self, name: str, now: float) -> tuple[float | None, float | None]:
        """按最近的速度：（用到 100% 的时刻, 到重置时会用到多少）。

        重置前用不完时前者是 None；没在用、数据不够时两个都是 None。name 是 five 或 seven。
        """
        value, rate, reset = getattr(self, name), getattr(self, f"{name}_rate"), getattr(self, f"{name}_reset")
        if value is None or rate is None or rate < PACE[name][2]:
            return None, None
        left = reset is not None and reset > now
        projected = value + rate * (reset - now) / 3600 if left else None
        empty = now + max(0.0, 100 - value) / rate * 3600
        if left and empty >= reset:
            empty = None
        return empty, projected


# ---------- 桌面端的额度历史 ----------


def series(samples: list[dict], key: str) -> list[tuple[float, float]]:
    return [(s["t"] / 1000, float(s["u"][key])) for s in samples if isinstance(s["u"].get(key), (int, float))]


def five_hour_reset(points: list[tuple[float, float]]) -> float | None:
    """5 小时窗口从额度空闲后的第一次使用开始计时。

    从最新往回找最近一次“0 变成正数”或“明显回落”的相邻两条记录，窗口起点就在两者之间，
    取中点再加 5 小时。拿历史记录核对过，误差在采样间隔的一半以内（约 8 分钟）。
    """
    if not points or points[-1][1] <= 0:
        return None
    for (t0, v0), (t1, v1) in zip(reversed(points[:-1]), reversed(points[1:])):
        if v1 > 0 and (v0 == 0 or v1 <= v0 - 3):
            if t1 - t0 > MAX_GAP:
                return None
            reset = (t0 + t1) / 2 + FIVE_HOURS
            return reset if reset > points[-1][0] else None
    return None


def seven_day_reset(points: list[tuple[float, float]], now: float) -> float | None:
    """7 天额度在每周固定的时刻重置：取近 5 周里间隔最短的一次回落，按 7 天周期推到下一次。"""
    best = None
    for (t0, v0), (t1, v1) in zip(points, points[1:]):
        if t1 < now - 5 * WEEK:
            continue
        if (v0 > 0 and v1 == 0) or v1 <= v0 - 5:
            if best is None or t1 - t0 < best[0]:
                best = (t1 - t0, (t0 + t1) / 2)
    if best is None or best[0] > 12 * 3600:
        return None
    mid = best[1]
    return mid + max(1, math.ceil((now - mid) / WEEK)) * WEEK


def summarize(samples: list[dict], now: float, missing: bool) -> Usage:
    five = series(samples, "fh")
    seven = series(samples, "sd")
    return Usage(
        five=five[-1][1] if five else None,
        seven=seven[-1][1] if seven else None,
        at=samples[-1]["t"] / 1000 if samples else None,
        five_reset=five_hour_reset(five),
        seven_reset=seven_day_reset(seven, now),
        missing=missing,
    )


class UsageReader:
    """文件的修改时间或大小变了才重新解析；碰上写到一半的文件就等下一轮。"""

    def __init__(self, path: Path) -> None:
        self.path = path
        self.stamp: tuple[int, int] | None = None
        self.samples: list[dict] = []
        self.missing = False

    def poll(self) -> bool:
        try:
            st = self.path.stat()
        except OSError:
            changed = not self.missing
            self.missing = True
            return changed
        stamp = (st.st_mtime_ns, st.st_size)
        if stamp == self.stamp:
            return False
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return False
        raw = data.get("samples") if isinstance(data, dict) else None
        samples = [
            s
            for s in raw or []
            if isinstance(s, dict) and isinstance(s.get("t"), (int, float)) and isinstance(s.get("u"), dict)
        ]
        samples.sort(key=lambda s: s["t"])
        if samples:
            # 只看最新记录所属账号的数据；账号标识只拿来比较，不显示也不保存。
            org = samples[-1].get("org")
            samples = [s for s in samples if s.get("org") == org]
        self.samples = samples
        self.stamp = stamp
        self.missing = False
        return True


# ---------- 插件写的会话文件 ----------


def number(value) -> float | None:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    return float(value) if math.isfinite(value) else None


def iso_time(text) -> float | None:
    if not isinstance(text, str):
        return None
    try:
        return datetime.fromisoformat(text.replace("Z", "+00:00")).timestamp()
    except ValueError:
        return None


@dataclass
class Turn:
    state: str = "idle"  # busy：Claude Code 正在处理这一轮；idle：等你输入
    seq: int = 0  # 第几轮，每开始一轮加一
    started: float | None = None  # 秒
    ended: float | None = None
    reason: str | None = None  # answer 正常答完，aborted 被打断，error 出错，refusal 拒答，ended 会话结束
    seconds: float = 0.0  # 这一轮用了多久


@dataclass
class LiveSession:
    updated: float  # 插件最后一次写这个文件的时间（秒）
    limits: dict[str, tuple[float, float | None]]  # five_hour、seven_day：（已用百分比, 重置时刻）
    limits_at: float  # 这份额度读数的时间
    tokens: int | None
    window: int | None
    cost: float | None
    measured_at: float
    turn: Turn = field(default_factory=Turn)
    ended: bool = False

    def alive(self, now: float) -> bool:
        return not self.ended and now - self.updated < LIVE_FRESH

    def busy(self, now: float) -> bool:
        return self.alive(now) and self.turn.state == "busy"

    def active_at(self) -> float:
        return max(self.measured_at, self.turn.started or 0, self.turn.ended or 0)


def parse_live(data) -> LiveSession | None:
    """插件写的一份会话文件；格式不认识时返回 None。毫秒换算成秒。"""
    if not isinstance(data, dict) or data.get("v") != 1:
        return None
    updated = number(data.get("updated"))
    if updated is None:
        return None

    def seconds(value) -> float | None:
        v = number(value)
        return v / 1000 if v else None

    limits = {}
    for item in data.get("limits") if isinstance(data.get("limits"), list) else []:
        if isinstance(item, dict) and isinstance(item.get("kind"), str):
            pct = number(item.get("percentUsed"))
            if pct is not None:
                limits[item["kind"]] = (pct, iso_time(item.get("resetsAt")))
    ctx = data.get("context") if isinstance(data.get("context"), dict) else {}
    raw = data.get("turn") if isinstance(data.get("turn"), dict) else {}
    tokens, window = number(ctx.get("tokens")), number(ctx.get("window"))
    turn = Turn(
        state="busy" if raw.get("state") == "busy" else "idle",
        seq=int(number(raw.get("seq")) or 0),
        started=seconds(raw.get("startedAt")),
        ended=seconds(raw.get("endedAt")),
        reason=raw.get("reason") if isinstance(raw.get("reason"), str) else None,
        seconds=(number(raw.get("durationMs")) or 0) / 1000,
    )
    return LiveSession(
        updated=updated / 1000,
        limits=limits,
        limits_at=seconds(data.get("limitsAt")) or 0.0,
        tokens=int(tokens) if tokens is not None else None,
        window=int(window) if window else None,
        cost=number(data.get("cost")),
        measured_at=seconds(data.get("measuredAt")) or 0.0,
        turn=turn,
        ended=data.get("ended") is True,
    )


class LiveReader:
    """读插件写的会话文件：只重新解析改动过的；超过三天没更新的文件顺手删掉。"""

    def __init__(self, folder: Path) -> None:
        self.folder = folder
        self.sessions: dict[str, LiveSession] = {}
        self.stamps: dict[str, tuple[int, int]] = {}

    def poll(self, now: float) -> bool:
        changed = False
        seen = set()
        try:
            entries = list(os.scandir(self.folder))
        except OSError:
            entries = []
        for entry in entries:
            if not entry.name.endswith(".json") or not entry.is_file(follow_symlinks=False):
                continue
            try:
                st = entry.stat(follow_symlinks=False)
            except OSError:
                continue
            if now - st.st_mtime > LIVE_KEEP:
                try:
                    os.remove(entry.path)
                except OSError:
                    pass
                continue
            seen.add(entry.name)
            stamp = (st.st_mtime_ns, st.st_size)
            if self.stamps.get(entry.name) == stamp:
                continue
            try:
                data = json.loads(Path(entry.path).read_text(encoding="utf-8"))
            except (OSError, ValueError):
                continue  # 插件正写到一半，下一轮再读
            self.stamps[entry.name] = stamp
            session = parse_live(data)
            if session is None:
                changed |= self.sessions.pop(entry.name, None) is not None
            else:
                self.sessions[entry.name] = session
                changed = True
        for name in [n for n in self.sessions if n not in seen]:
            del self.sessions[name]
            self.stamps.pop(name, None)
            changed = True
        return changed

    def reading(self) -> tuple[dict[str, tuple[float, float | None]], float] | None:
        """所有会话里最新的一份额度读数：（各项额度, 读数时间）。"""
        best = max((s for s in self.sessions.values() if s.limits), key=lambda s: s.limits_at, default=None)
        return (best.limits, best.limits_at) if best else None

    def current(self, now: float, active: str | None = None) -> LiveSession | None:
        """上下文和花费看哪个会话。active 是会话记录里最近有动静的会话 id：它没有插件数据（比如装插件之前开的）
        或者已经关了，就返回 None，不拿别的会话顶替。不知道 active 时，取还开着的会话里最近有动静的那个。"""
        if active is not None:
            session = self.sessions.get(f"{active}.json")
            return session if session is not None and session.alive(now) else None
        return max((s for s in self.sessions.values() if s.alive(now)), key=LiveSession.active_at, default=None)

    def alive(self, now: float) -> bool:
        return any(s.alive(now) for s in self.sessions.values())

    def busy(self, now: float) -> bool:
        return any(s.busy(now) for s in self.sessions.values())


# ---------- 合并与预测 ----------


def pace(points: list[tuple[float, float]], now: float, value: float | None, start: float | None, name: str) -> float | None:
    """最近一段时间平均每小时用掉的百分比；数据跨度不够时返回 None。

    points 是（时刻, 百分比），value 是现在的值，start 是当前窗口开始的时刻（不知道时为 None）。
    只看窗口开始以后、最近一段时间里的记录；中间有明显回落（重置）就从回落之后算起；
    窗口开始不久时，开头按 0 算。
    """
    if value is None:
        return None
    lookback, shortest, _ = PACE[name]
    begin = now - lookback if start is None else max(now - lookback, start)
    pts = [(t, v) for t, v in points if begin <= t <= now]
    for i in range(len(pts) - 1, 0, -1):
        if pts[i][1] < pts[i - 1][1] - 2:
            pts = pts[i:]
            break
    if start is not None and start >= now - lookback and (not pts or pts[0][0] > start):
        pts.insert(0, (start, 0.0))
    if not pts or now - pts[0][0] < shortest:
        return None
    return max(0.0, (value - pts[0][1]) / (now - pts[0][0]) * 3600)


KINDS = {"five": "five_hour", "seven": "seven_day"}


def merge(
    hist: Usage,
    reading: tuple[dict[str, tuple[float, float | None]], float] | None,
    session: LiveSession | None,
    points: dict[str, list[tuple[float, float]]],
    now: float,
) -> Usage:
    """把插件的读数并进桌面端历史推出来的结果，再算消耗速度。

    读数谁新用谁；插件给的重置时间只要晚于历史的最新记录（说明还是同一个窗口）就用它，不再标“约”。
    """
    u = replace(hist)
    if reading is not None:
        limits, at = reading
        for name, kind in KINDS.items():
            if kind not in limits:
                continue
            value, reset = limits[kind]
            if hist.at is None or at >= hist.at - 60:
                setattr(u, name, value)
                if reset is not None:
                    setattr(u, f"{name}_reset", reset)
                    setattr(u, f"{name}_exact", True)
            elif reset is not None and reset > hist.at:
                setattr(u, f"{name}_reset", reset)
                setattr(u, f"{name}_exact", True)
        u.at = max(hist.at or 0.0, at) or None
        u.missing = False
    if session is not None:
        u.tokens, u.window, u.cost = session.tokens, session.window, session.cost
    if not u.stale(now):
        for name in PACE:
            reset = getattr(u, f"{name}_reset")
            if reset is not None and reset <= now:
                continue  # 这个窗口已经结束，新窗口还没有数据
            start = reset - SPANS[name] if reset is not None else None
            setattr(u, f"{name}_rate", pace(points.get(name, []), now, getattr(u, name), start, name))
    return u


# ---------- 文字 ----------


def when(ts: float, now: float) -> str:
    d = datetime.fromtimestamp(ts)
    days = (d.date() - datetime.fromtimestamp(now).date()).days
    clock = d.strftime("%H:%M")
    if days == 0:
        return clock
    if days == 1:
        return f"明天 {clock}"
    if days == -1:
        return f"昨天 {clock}"
    if 1 < days < 7:
        return f"周{WEEKDAYS[d.weekday()]} {clock}"
    return f"{d.month} 月 {d.day} 日 {clock}"


def after(prefix: str, text: str) -> str:
    """中文与数字之间留一个空格，与汉字相接则不留：约 14:22、约周二 01:04。"""
    return f"{prefix} {text}" if text[:1].isdigit() else f"{prefix}{text}"


def reset_text(ts: float | None, value: float | None, now: float, exact: bool = False) -> str:
    if value is not None and value <= 0:
        return "还没开始计时"
    if ts is None:
        return "重置时间暂时推算不出来"
    if ts <= now:
        return "已经重置了" if exact else "按推算应该已经重置了"
    return f"{when(ts, now)} 重置" if exact else after("约", f"{when(ts, now)} 重置")


def tone(pct: float) -> str:
    return "danger" if pct >= 90 else "warn" if pct >= 70 else "ok"


def tokens_text(n: int) -> str:
    if n >= 1_000_000:
        return f"{n / 1_000_000:.1f}".rstrip("0").rstrip(".") + "M"
    if n >= 1000:
        return f"{n / 1000:.1f}".rstrip("0").rstrip(".") + "k"
    return str(n)


def duration_text(seconds: float) -> str:
    s = max(0, round(seconds))
    if s < 60:
        return f"{s} 秒"
    if s < 3600:
        return f"{s // 60} 分 {s % 60} 秒" if s % 60 else f"{s // 60} 分钟"
    return f"{s // 3600} 小时 {s % 3600 // 60} 分" if s % 3600 // 60 else f"{s // 3600} 小时"


def fields(u: Usage, now: float, extra: dict | None = None) -> dict:
    """台词里花括号字段的实时数据；缺数据的字段不放进来，用到它的句子会被跳过。"""
    out = {
        "five_reset": reset_text(u.five_reset, u.five, now, u.five_exact),
        "seven_reset": reset_text(u.seven_reset, u.seven, now, u.seven_exact),
    }
    if u.five is not None:
        out["five"] = round(u.five)
        out["five_left"] = max(0, round(100 - u.five))
    if u.seven is not None:
        out["seven"] = round(u.seven)
        out["seven_left"] = max(0, round(100 - u.seven))
    if u.five_rate is not None and u.five_rate >= PACE["five"][2]:
        out["five_rate"] = max(1, round(u.five_rate))
    empty, _ = u.outlook("five", now)
    if empty is not None:
        out["five_empty"] = when(empty, now)
    if u.tokens is not None:
        out["ctx"] = tokens_text(u.tokens)
        if u.window:
            out["ctx_pct"] = round(u.tokens / u.window * 100)
    if u.cost is not None:
        out["cost"] = f"${u.cost:.2f}"
    out.update(extra or {})
    return out
