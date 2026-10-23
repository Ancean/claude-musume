"""line_tags.py 的测试：闲聊区台词开头的【条件】怎么拆、怎么检查、什么时候算数。只用标准库。"""

from __future__ import annotations

import sys
import unittest
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import line_tags as t  # noqa: E402


class LineTags(unittest.TestCase):
    def test_split_and_join(self):
        self.assertEqual(t.split("【见面】【深夜】 这么晚还来？"), (["见面", "深夜"], "这么晚还来？"))
        self.assertEqual(t.split("[早上]早"), (["早上"], "早"))
        self.assertEqual(t.split("没有条件"), ([], "没有条件"))
        self.assertEqual(t.split("【没写完 的条件"), ([], "【没写完 的条件"))
        self.assertEqual(t.join(["深夜", "见面"], "这么晚还来？"), "【见面】【深夜】这么晚还来？")

    def test_problem(self):
        self.assertIsNone(t.problem([], "greet_morning"))
        self.assertIsNone(t.problem(["见面", "深夜"], "chat"))
        self.assertIn("闲聊区", t.problem(["见面"], "done"))
        self.assertIn("不是可用的条件", t.problem(["周末"], "chat"))
        self.assertIn("只能选一个", t.problem(["见面", "今天第一次"], "chat"))
        self.assertEqual(t.problem(["早上", "深夜"], "chat"), "时段只能选一个")

    def test_periods(self):
        hours = {4: "night", 5: "morning", 9: "morning", 10: "day", 16: "day", 17: "evening", 22: "evening", 23: "night"}
        for hour, period in hours.items():
            self.assertEqual(t.period_of(hour), period, hour)
        self.assertTrue(t.fits([], "night"))
        self.assertTrue(t.fits(["见面", "深夜"], "night"))
        self.assertFalse(t.fits(["早上"], "night"))

    def test_day_starts_at_five(self):
        # 熬夜过了零点还算前一天。
        self.assertEqual(t.day_key(datetime(2026, 10, 22, 4, 59)), "2026-10-21")
        self.assertEqual(t.day_key(datetime(2026, 10, 22, 5, 0)), "2026-10-22")


if __name__ == "__main__":
    unittest.main()
