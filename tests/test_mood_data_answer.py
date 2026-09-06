# -*- coding: utf-8 -*-
"""🧭🫧 §2.10 被問座標就一定要有數字＋對話史要等於真的說出口的話。

截圖 18:44：使用者問「你現在情緒座標怎樣啦」，回覆只有「我現在的感覺，嗯，是比較興奮、雀躍的」
——一個數字都沒有（使用者：「情緒座標不是有具體的數據嗎？」）。
實測：偵測器**有命中**、hint 也寫著「V/A 只准照抄」，但那個 hint 只掛在兩個呼叫點 ⇒ 走別條路由就拿不到。
全 stub、零網路。
"""

import io
import os
import re
import tempfile
import unittest
from types import SimpleNamespace

from telegram_monitor import circumplex, monitor, persona
from telegram_monitor.state import State


class DetectorFiresButGroundNeverArrivedTest(unittest.TestCase):
    """釘住根因：不是偵測器沒中，是接地只掛在兩個呼叫點。"""

    def test_detector_hits_the_screenshot_line(self):
        self.assertTrue(circumplex.is_mood_data_question("你現在情緒座標怎樣啦"))

    def test_hint_already_demanded_the_numbers(self):
        self.assertIn("只准照抄", persona.MOOD_COORD_HINT)      # 條文一直都在，是它沒到場


class CoordLineTest(unittest.TestCase):
    def _state(self):
        return State(os.path.join(tempfile.mkdtemp(), "s.json"))

    def test_line_carries_real_numbers(self):
        line = circumplex.coord_line(self._state())
        self.assertRegex(line, re.compile(r"V [+-]\d+\.\d\d"))
        self.assertRegex(line, re.compile(r"A [+-]\d+\.\d\d"))

    def test_numbers_come_from_position(self):
        s = self._state()
        v, a = circumplex.position(s)
        self.assertIn(f"V {v:+.2f}", circumplex.coord_line(s))


class SayBackstopTest(unittest.TestCase):
    class Cl:
        def __init__(self):
            self.sent, self.dry_run = [], False

        def send(self, t):
            self.sent.append(t)
            return True

    def _out(self, text, armed):
        monitor._TURN.clear()
        monitor._TURN["bubbles"] = None
        if armed:
            monitor._TURN["mood_data_line"] = "（我此刻的座標是 V +0.35、A +0.20——落在「興奮」那一帶。）"
        cl = self.Cl()
        monitor._say(cl, text)
        return cl.sent

    def test_appends_when_the_reply_has_no_numbers(self):
        sent = self._out("我現在的感覺，嗯，是比較興奮、雀躍的。", True)
        self.assertTrue(any("V +0.35" in x for x in sent))
        self.assertIn("我現在的感覺，嗯，是比較興奮、雀躍的。", sent)   # ★ 只增不減：原文一字不動

    def test_silent_when_the_reply_already_has_numbers(self):
        sent = self._out("我此刻 V +0.35、A +0.20，偏興奮。", True)
        self.assertEqual(len([x for x in sent if "座標是" in x]), 0)

    def test_not_armed_is_unchanged(self):
        t = "我現在的感覺，嗯，是比較興奮、雀躍的。"
        self.assertEqual(self._out(t, False), [t])


class HistoryMatchesWhatWasSentTest(unittest.TestCase):
    """🫧 §2.10 §1.29 那一族：歷史 ≠ 真的說過的話，是「誰在說話」錯亂的溫床。"""

    class Cl:
        def __init__(self):
            self.sent, self.dry_run = [], False

        def send(self, t):
            self.sent.append(t)
            return True

    def test_history_equals_sent_after_stripping(self):
        s = State(os.path.join(tempfile.mkdtemp(), "s.json"))
        t = "好，我明白了。我會直接猜。那我猜，你是處女座？"
        monitor._TURN.clear()
        monitor._TURN.update({"bubbles": None, "act_first": True})
        cl = self.Cl()
        monitor._say(cl, t)
        monitor._remember(s, "model", t)
        self.assertEqual(s.convo_history[-1]["text"], "".join(cl.sent))
        self.assertNotIn("好，我明白了。", s.convo_history[-1]["text"])   # 沒說出口的不進歷史

    def test_untouched_when_nothing_was_stripped(self):
        s = State(os.path.join(tempfile.mkdtemp(), "s.json"))
        t = "那我猜，你是處女座？"
        monitor._TURN.clear()
        monitor._TURN.update({"bubbles": None, "act_first": True})
        monitor._say(self.Cl(), t)
        monitor._remember(s, "model", t)
        self.assertEqual(s.convo_history[-1]["text"], t)

    def test_registration_is_turn_scoped(self):
        src = io.open("telegram_monitor/monitor.py", encoding="utf-8").read()
        self.assertIn('_TURN.pop("act_first_sent", None)', src)   # 跨輪殘值會把別輪的話換掉


class ConfigTest(unittest.TestCase):
    def test_flag_synced(self):
        src = io.open("telegram_monitor/config.py", encoding="utf-8").read()
        self.assertIn("MOOD_DATA_ANSWER", src)
        self.assertIn("mood_data_answer_enabled", src)
        self.assertRegex(io.open(".env.example", encoding="utf-8").read(), re.compile(r"^MOOD_DATA_ANSWER=1", re.M))
        self.assertIn("MOOD_DATA_ANSWER", io.open("README.md", encoding="utf-8").read())
        self.assertFalse(getattr(SimpleNamespace(), "mood_data_answer_enabled", False))


if __name__ == "__main__":
    unittest.main()
