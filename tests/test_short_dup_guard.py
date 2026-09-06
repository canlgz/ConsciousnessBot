# -*- coding: utf-8 -*-
"""🦜 §2.13 短句 ack 的重複守門：「好，記下來了。」不准連送三次。

截圖 20:15–20:16：同一句「好，記下來了。」送了三次（使用者：「重複說同一句話兩次，老人癡呆了啊」）。
根因＝§1.71 重播守門**刻意不記** `_REPLAY_MIN_LEN`(10) 字以下的泡泡——原意是「嗯／好」這種合理的短重複
不該被擋，代價是**整則就只有一句短 ack 時，它完全在防線外**。全 stub、零網路。
"""

import io
import re
import unittest
from types import SimpleNamespace

from telegram_monitor import echo, monitor

ACK = "好，記下來了。"


class RootCauseTest(unittest.TestCase):
    def test_the_ack_is_below_the_replay_threshold(self):
        # 釘住根因：它短到 §1.71 根本不記、當然也不比對
        self.assertLess(len(echo._norm(ACK)), monitor._REPLAY_MIN_LEN)


class ShortDupTest(unittest.TestCase):
    def setUp(self):
        monitor._SHORT_SENT.clear()

    def test_identical_short_line_is_not_sent_twice(self):
        monitor._short_note(ACK, now_ts=1000.0)
        self.assertTrue(monitor._short_dup_hit(ACK, now_ts=1010.0))

    def test_window_expires(self):
        monitor._short_note(ACK, now_ts=1000.0)
        self.assertFalse(monitor._short_dup_hit(ACK, now_ts=1000.0 + monitor._SHORT_DUP_SEC + 1))

    def test_different_short_line_still_goes(self):
        monitor._short_note(ACK, now_ts=1000.0)
        self.assertFalse(monitor._short_dup_hit("好，我等你。", now_ts=1010.0))

    def test_long_lines_are_left_to_the_replay_guard(self):
        long_line = "我記得我們聊過，但我沒有把那個最終的結果記進帳本，所以現在回頭看，它沒有浮現。"
        monitor._short_note(long_line, now_ts=1000.0)
        self.assertFalse(monitor._short_dup_hit(long_line, now_ts=1010.0))   # 不歸這道守門管

    def test_ring_is_bounded(self):
        for i in range(80):
            monitor._short_note(f"好{i}", now_ts=1000.0 + i)
        self.assertLessEqual(len(monitor._SHORT_SENT), 24)


class SayWiringTest(unittest.TestCase):
    class Cl:
        def __init__(self):
            self.sent, self.dry_run = [], False

        def send(self, t):
            self.sent.append(t)
            return True

    def setUp(self):
        monitor._SHORT_SENT.clear()

    def _say(self, cl, text, on=True):
        monitor._TURN.clear()
        monitor._TURN.update({"bubbles": None, "short_dup_guard": on})
        return monitor._say(cl, text)

    def test_three_identical_acks_become_one(self):
        cl = self.Cl()
        for _ in range(3):
            self._say(cl, ACK)
        self.assertEqual(cl.sent, [ACK])

    def test_returns_true_so_bookkeeping_is_unaffected(self):
        cl = self.Cl()
        self._say(cl, ACK)
        self.assertIs(self._say(cl, ACK), True)      # 那句話幾十秒前才真的送達過，不該被記成「沒送出去」

    def test_flag_off_is_unchanged(self):
        cl = self.Cl()
        for _ in range(3):
            self._say(cl, ACK, on=False)
        self.assertEqual(len(cl.sent), 3)

    def test_note_happens_only_on_real_send(self):
        src = io.open("telegram_monitor/monitor.py", encoding="utf-8").read()
        i = src.index("_short_note(b)")
        self.assertIn("_replay_note(b)", src[i - 200:i])   # 與 §1.71 同一個「真的送出去了」的點


class ConfigTest(unittest.TestCase):
    def test_flag_synced(self):
        src = io.open("telegram_monitor/config.py", encoding="utf-8").read()
        self.assertIn("SHORT_DUP_GUARD", src)
        self.assertIn("short_dup_guard_enabled", src)
        self.assertRegex(io.open(".env.example", encoding="utf-8").read(), re.compile(r"^SHORT_DUP_GUARD=1", re.M))
        self.assertIn("SHORT_DUP_GUARD", io.open("README.md", encoding="utf-8").read())
        self.assertFalse(getattr(SimpleNamespace(), "short_dup_guard_enabled", False))


if __name__ == "__main__":
    unittest.main()
