"""usage_data.py 的测试：重置时间的推算、两个来源的读取与合并、消耗速度、显示文字。只用标准库。

运行（在 buddy-pet 文件夹里）：python -m unittest discover -s tests
"""

from __future__ import annotations

import json
import os
import sys
import tempfile
import time
import unittest
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import usage_data as u  # noqa: E402

H = 3600
DAY = 86400
T = 1_790_000_000.0  # 一个固定的“现在”，秒
STEP = 15 * 60  # 桌面端大约每 15 分钟记一次


def sample(t: float, fh: float | None = None, sd: float | None = None, org: str = "a") -> dict:
    usage = {k: v for k, v in (("fh", fh), ("sd", sd)) if v is not None}
    return {"t": t * 1000, "u": usage, "org": org}


class FiveHourReset(unittest.TestCase):
    def test_from_first_use_after_idle(self):
        # 0 变成正数的两条记录之间开始计时，取中点加 5 小时。
        points = [(T, 0.0), (T + STEP, 0.0), (T + 2 * STEP, 10.0), (T + 3 * STEP, 20.0)]
        self.assertEqual(u.five_hour_reset(points), T + 1.5 * STEP + 5 * H)

    def test_from_a_clear_drop(self):
        # 没回到 0、但明显回落，也是新窗口开始了。
        points = [(T, 80.0), (T + STEP, 4.0), (T + 2 * STEP, 9.0)]
        self.assertEqual(u.five_hour_reset(points), T + 0.5 * STEP + 5 * H)

    def test_small_dip_is_not_a_reset(self):
        points = [(T, 0.0), (T + STEP, 30.0), (T + 2 * STEP, 28.0), (T + 3 * STEP, 35.0)]
        self.assertEqual(u.five_hour_reset(points), T + 0.5 * STEP + 5 * H)

    def test_unknown_when_records_too_far_apart(self):
        points = [(T, 0.0), (T + u.MAX_GAP + 60, 10.0)]
        self.assertIsNone(u.five_hour_reset(points))

    def test_unknown_when_idle_or_window_over(self):
        self.assertIsNone(u.five_hour_reset([]))
        self.assertIsNone(u.five_hour_reset([(T, 10.0), (T + STEP, 0.0)]))
        # 推出来的重置时刻已经早于最新一条记录：这个窗口早就结束了。
        points = [(T, 0.0), (T + STEP, 10.0), (T + 6 * H, 12.0)]
        self.assertIsNone(u.five_hour_reset(points))


class SevenDayReset(unittest.TestCase):
    def setUp(self):
        self.mid = T + 0.5 * STEP
        self.points = [(T, 60.0), (T + STEP, 0.0), (T + 2 * STEP, 3.0)]

    def test_next_weekly_reset(self):
        self.assertEqual(u.seven_day_reset(self.points, T + 2 * DAY), self.mid + 7 * DAY)
        # 过了一周还没看到新的回落：按 7 天周期往后推。
        self.assertEqual(u.seven_day_reset(self.points, T + 8 * DAY), self.mid + 14 * DAY)

    def test_picks_the_drop_with_the_shortest_gap(self):
        points = [(T - 7 * DAY - 6 * H, 50.0), (T - 7 * DAY + 4 * H, 0.0), *self.points]
        self.assertEqual(u.seven_day_reset(points, T + DAY), self.mid + 7 * DAY)

    def test_unknown_when_drop_is_vague_or_old(self):
        self.assertIsNone(u.seven_day_reset([(T, 60.0), (T + 13 * H, 0.0)], T + DAY))
        self.assertIsNone(u.seven_day_reset(self.points, T + 6 * 7 * DAY))
        self.assertIsNone(u.seven_day_reset([(T, 10.0), (T + STEP, 12.0)], T + DAY))


class DesktopHistory(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.TemporaryDirectory()
        self.path = Path(self.dir.name) / "plan-usage-history.json"

    def tearDown(self):
        self.dir.cleanup()

    def write(self, data) -> None:
        self.path.write_text(json.dumps(data) if not isinstance(data, str) else data, encoding="utf-8")
        # 文件的修改时间和大小没变时不会重新解析，测试里写得快，手动把时间往后挪。
        st = self.path.stat()
        os.utime(self.path, ns=(st.st_atime_ns, st.st_mtime_ns + 1_000_000_000))

    def test_missing_file(self):
        reader = u.UsageReader(self.path)
        self.assertTrue(reader.poll())
        self.assertTrue(reader.missing)
        self.assertFalse(reader.poll())  # 一直找不到时不再报“变了”
        self.assertTrue(u.summarize(reader.samples, T, reader.missing).missing)

    def test_reads_sorted_samples_of_the_latest_account(self):
        self.write(
            {
                "samples": [
                    sample(T + 2 * STEP, 20, 41),
                    sample(T + STEP, 99, 99, org="b"),  # 别的账号
                    sample(T, 0, 40),
                    sample(T + STEP, 10, 40),
                    {"t": "bad"},
                    "bad",
                ]
            }
        )
        reader = u.UsageReader(self.path)
        self.assertTrue(reader.poll())
        self.assertEqual([s["t"] / 1000 for s in reader.samples], [T, T + STEP, T + 2 * STEP])
        usage = u.summarize(reader.samples, T + 2 * STEP, reader.missing)
        self.assertEqual((usage.five, usage.seven, usage.at), (20.0, 41.0, T + 2 * STEP))
        self.assertEqual(usage.five_reset, T + 0.5 * STEP + 5 * H)
        self.assertFalse(usage.five_exact)

    def test_half_written_file_keeps_last_reading(self):
        self.write({"samples": [sample(T, 5, 1)]})
        reader = u.UsageReader(self.path)
        reader.poll()
        self.write('{"samples": [')
        self.assertFalse(reader.poll())
        self.assertEqual(len(reader.samples), 1)


def live_file(session: str = "s1", updated: float = T, **extra) -> dict:
    body = {
        "v": 1,
        "session": session,
        "updated": updated * 1000,
        "limits": [
            {"kind": "five_hour", "percentUsed": 23.5, "resetsAt": "2026-10-04T12:00:00Z"},
            {"kind": "seven_day", "percentUsed": 40},
        ],
        "limitsAt": updated * 1000,
        "context": {"tokens": 32000, "window": 200000},
        "cost": 1.25,
        "measuredAt": updated * 1000,
        "turn": {"state": "busy", "seq": 3, "startedAt": (updated - 30) * 1000},
        "ended": False,
    }
    body.update(extra)
    return body


class PluginFiles(unittest.TestCase):
    def test_parse(self):
        s = u.parse_live(live_file())
        self.assertEqual(s.updated, T)
        self.assertEqual(s.limits["five_hour"], (23.5, datetime.fromisoformat("2026-10-04T12:00:00+00:00").timestamp()))
        self.assertEqual(s.limits["seven_day"], (40.0, None))
        self.assertEqual((s.tokens, s.window, s.cost), (32000, 200000, 1.25))
        self.assertEqual((s.turn.state, s.turn.seq, s.turn.started), ("busy", 3, T - 30))
        self.assertTrue(s.busy(T + 10))
        self.assertFalse(s.alive(T + u.LIVE_FRESH))

    def test_rejects_unknown_formats_and_odd_values(self):
        self.assertIsNone(u.parse_live(live_file(v=2)))
        self.assertIsNone(u.parse_live({"v": 1}))
        self.assertIsNone(u.parse_live([]))
        s = u.parse_live(live_file(cost=True, context={"tokens": "many"}, turn={"state": "weird", "seq": None}))
        self.assertIsNone(s.cost)  # True 不算数字
        self.assertIsNone(s.tokens)
        self.assertEqual((s.turn.state, s.turn.seq), ("idle", 0))

    def test_ended_session_is_not_alive(self):
        self.assertFalse(u.parse_live(live_file(ended=True)).alive(T))


class PluginFolder(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.TemporaryDirectory()
        self.folder = Path(self.dir.name)
        self.reader = u.LiveReader(self.folder)

    def tearDown(self):
        self.dir.cleanup()

    def put(self, name: str, body, age: float = 0) -> Path:
        path = self.folder / name
        path.write_text(json.dumps(body), encoding="utf-8")
        mtime = time.time() - age
        os.utime(path, (mtime, mtime))
        return path

    def test_reading_current_and_cleanup(self):
        now = time.time()
        self.put("old.json", live_file("old", now - 60, limitsAt=(now - 600) * 1000))
        self.put("new.json", live_file("new", now - 5))
        stale = self.put("gone.json", live_file("gone", now - 4 * DAY), age=4 * DAY)
        self.put("notes.txt", "不是插件的文件")
        self.assertTrue(self.reader.poll(now))
        self.assertFalse(stale.exists())  # 超过三天的会话文件顺手删掉
        self.assertTrue((self.folder / "notes.txt").exists())
        self.assertEqual(sorted(self.reader.sessions), ["new.json", "old.json"])
        self.assertFalse(self.reader.poll(now))  # 没改动就不重新解析

        _, at = self.reader.reading()
        self.assertAlmostEqual(at, now - 5)  # 额度看读数最新的那个会话
        self.assertAlmostEqual(self.reader.current(now, "old").updated, now - 60)
        # 最近有动静的会话没有插件数据：不拿别的会话顶替。
        self.assertIsNone(self.reader.current(now, "unknown"))
        self.assertAlmostEqual(self.reader.current(now).updated, now - 5)
        self.assertTrue(self.reader.busy(now))

        (self.folder / "new.json").unlink()
        self.assertTrue(self.reader.poll(now))
        self.assertEqual(list(self.reader.sessions), ["old.json"])

    def test_half_written_file_is_read_next_time(self):
        now = time.time()
        path = self.folder / "s1.json"
        path.write_text('{"v": 1, "upd', encoding="utf-8")
        self.assertFalse(self.reader.poll(now))
        path.write_text(json.dumps(live_file("s1", now)), encoding="utf-8")
        st = path.stat()
        os.utime(path, ns=(st.st_atime_ns, st.st_mtime_ns + 1_000_000_000))
        self.assertTrue(self.reader.poll(now))
        self.assertIn("s1.json", self.reader.sessions)


class Pace(unittest.TestCase):
    def test_rate_over_the_last_hour(self):
        points = [(T - 2 * H, 0.0), (T - H, 10.0), (T - H / 2, 20.0)]
        self.assertAlmostEqual(u.pace(points, T, 30.0, None, "five"), 20.0)

    def test_window_that_just_started_counts_from_zero(self):
        start = T - 20 * 60
        self.assertAlmostEqual(u.pace([(T - 5 * 60, 8.0)], T, 10.0, start, "five"), 30.0)

    def test_counts_only_after_a_reset_inside_the_lookback(self):
        points = [(T - 50 * 60, 90.0), (T - 40 * 60, 2.0), (T - 20 * 60, 8.0)]
        self.assertAlmostEqual(u.pace(points, T, 14.0, None, "five"), 12 / (40 * 60) * H)

    def test_not_enough_data(self):
        self.assertIsNone(u.pace([(T - 5 * 60, 10.0)], T, 12.0, None, "five"))
        self.assertIsNone(u.pace([(T - H, 10.0)], T, None, None, "five"))

    def test_outlook(self):
        usage = u.Usage(five=50, five_rate=20, five_reset=T + H)
        self.assertEqual(usage.outlook("five", T), (None, 70))  # 重置前用不完
        usage.five_reset = T + 3 * H
        empty, projected = usage.outlook("five", T)
        self.assertAlmostEqual(empty, T + 2.5 * H)
        self.assertAlmostEqual(projected, 110)
        self.assertEqual(u.Usage(five=50, five_rate=0.1).outlook("five", T), (None, None))  # 几乎没在用


class Merge(unittest.TestCase):
    def setUp(self):
        self.hist = u.Usage(five=30, seven=40, at=T - 600, five_reset=T + 2 * H, seven_reset=T + 3 * DAY)
        self.session = u.parse_live(live_file(updated=T))

    def test_newer_plugin_reading_wins_and_is_exact(self):
        reading = ({"five_hour": (35.0, T + 2 * H + 300), "seven_day": (41.0, None)}, T - 60)
        merged = u.merge(self.hist, reading, self.session, {}, T)
        self.assertEqual((merged.five, merged.seven, merged.at), (35.0, 41.0, T - 60))
        self.assertEqual(merged.five_reset, T + 2 * H + 300)
        self.assertTrue(merged.five_exact)
        self.assertEqual(merged.seven_reset, T + 3 * DAY)  # 插件没给 7 天的重置时间，沿用推算的
        self.assertFalse(merged.seven_exact)
        self.assertEqual((merged.tokens, merged.window, merged.cost), (32000, 200000, 1.25))

    def test_older_plugin_reading_only_lends_its_reset_time(self):
        reading = ({"five_hour": (20.0, T + 2 * H + 300)}, T - 3 * H)
        merged = u.merge(self.hist, reading, None, {}, T)
        self.assertEqual(merged.five, 30)
        self.assertEqual(merged.five_reset, T + 2 * H + 300)
        self.assertTrue(merged.five_exact)
        self.assertIsNone(merged.tokens)

    def test_rates_only_when_fresh(self):
        points = {"five": [(T - H, 10.0), (T - 30 * 60, 20.0)]}
        hist = u.Usage(five=30, at=T - 60)
        self.assertAlmostEqual(u.merge(hist, None, None, points, T).five_rate, 20.0)
        stale = u.Usage(five=30, at=T - u.STALE_AFTER - 60)
        self.assertIsNone(u.merge(stale, None, None, points, T).five_rate)


class Texts(unittest.TestCase):
    def setUp(self):
        self.now = datetime(2026, 10, 21, 12, 0).timestamp()  # 星期三

    def at(self, *args) -> float:
        return datetime(*args).timestamp()

    def test_when(self):
        self.assertEqual(u.when(self.at(2026, 10, 21, 13, 0), self.now), "13:00")
        self.assertEqual(u.when(self.at(2026, 10, 22, 9, 5), self.now), "明天 09:05")
        self.assertEqual(u.when(self.at(2026, 10, 20, 23, 0), self.now), "昨天 23:00")
        self.assertEqual(u.when(self.at(2026, 10, 24, 1, 4), self.now), "周六 01:04")
        self.assertEqual(u.when(self.at(2026, 10, 28, 8, 0), self.now), "10 月 28 日 08:00")

    def test_reset_text(self):
        later = self.at(2026, 10, 21, 14, 22)
        self.assertEqual(u.reset_text(later, 10, self.now), "约 14:22 重置")
        self.assertEqual(u.reset_text(later, 10, self.now, exact=True), "14:22 重置")
        self.assertEqual(u.reset_text(self.at(2026, 10, 24, 1, 4), 10, self.now), "约周六 01:04 重置")
        self.assertEqual(u.reset_text(later, 0, self.now), "还没开始计时")
        self.assertEqual(u.reset_text(None, 10, self.now), "重置时间暂时推算不出来")
        self.assertEqual(u.reset_text(self.now - 60, 10, self.now), "按推算应该已经重置了")
        self.assertEqual(u.reset_text(self.now - 60, 10, self.now, exact=True), "已经重置了")

    def test_numbers(self):
        self.assertEqual([u.tokens_text(n) for n in (999, 1000, 48_200, 1_000_000)], ["999", "1k", "48.2k", "1M"])
        self.assertEqual(
            [u.duration_text(s) for s in (59, 60, 90, 3600, 3660)],
            ["59 秒", "1 分钟", "1 分 30 秒", "1 小时", "1 小时 1 分"],
        )
        self.assertEqual([u.tone(p) for p in (69.9, 70, 89.9, 90)], ["ok", "warn", "warn", "danger"])

    def test_fields_leave_out_missing_data(self):
        out = u.fields(u.Usage(five=62.4), self.now, {"task_time": "3 分钟"})
        self.assertEqual((out["five"], out["five_left"], out["task_time"]), (62, 38, "3 分钟"))
        for key in ("seven", "ctx", "ctx_pct", "cost", "five_rate", "five_empty"):
            self.assertNotIn(key, out)
        full = u.fields(u.Usage(five=50, five_rate=20, five_reset=self.now + 3 * H, tokens=48_200, window=200_000, cost=1.371), self.now)
        self.assertEqual((full["ctx"], full["ctx_pct"], full["cost"], full["five_rate"]), ("48.2k", 24, "$1.37", 20))
        self.assertEqual(full["five_empty"], "14:30")


if __name__ == "__main__":
    unittest.main()
