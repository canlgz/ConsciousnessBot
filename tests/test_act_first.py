# -*- coding: utf-8 -*-
"""🫧 §2.09 先做那件事（ACT_FIRST）：開頭那種零資訊的接話泡泡不要送。

截圖（13:46–14:29）：使用者連催八次（「你怎麼這麼多廢話」「猜啊」「你就直接猜吧」），
每一輪回覆都先來一到三顆純接話泡泡才進正題——使用者原話：「拖泥帶水」。
三道既有閘全瞎（本檔第一組測試把這件事釘死，免得下次又以為有人在守）。全 stub、零網路。
"""

import io
import re
import unittest
from types import SimpleNamespace

from telegram_monitor import echo, monitor, selfstate

# 截圖裡使用者真的打過的八句
NUDGES = ["你再根據我的記寫內容來再猜一次吧！", "你怎麼這麼多廢話", "我的星座是？", "直接猜我的星座是什麼",
          "猜啊", "在猜呀，你怎麼這麼多廢話？", "你就直接猜吧", "我是什麼星座？"]


class ExistingGatesAreBlindTest(unittest.TestCase):
    """釘住根因：不是這幾道閘沒調好，是它們**結構上看不到**這個情境。"""

    def test_promise_status_kind_sees_nothing(self):
        for t in NUDGES:
            self.assertEqual(selfstate.promise_status_kind(t), "", t)

    def test_stuck_signal_needs_identical_replies(self):
        # 它每次用**不同的話**拖延 ⇒ §1.87 的訊號永遠不亮
        a, b = "好我明白了我會直接猜但我手邊沒有直接的線索", "喔好抱歉我沒抓到你急著想知道的感覺"
        self.assertFalse(echo._looks_same(echo._norm(a), echo._norm(b), 0.8))


class AckOnlyTest(unittest.TestCase):
    def test_pure_acknowledgement(self):
        for b in ("好，我明白了。", "喔，好！", "抱歉！", "嗯。", "對不起。", "沒問題！", "好。", "OK"):
            self.assertTrue(monitor._ack_only_bubble(b), b)

    def test_real_content_survives(self):
        # ★ 實作期實測的陷阱：真正的答案新字比純宣告還少，所以判準不能用「帶多少新字」
        for b in ("那我猜，你是......處女座？", "好，那我就隨便猜一個喔。", "我沒抓到你急著想知道的感覺。",
                  "好，我明白了，你要的是天秤座那件事的答案。"):
            self.assertFalse(monitor._ack_only_bubble(b), b)

    def test_long_is_never_ack(self):
        self.assertFalse(monitor._ack_only_bubble("好" * 20))


class StripTest(unittest.TestCase):
    def test_strips_leading_only(self):
        self.assertEqual(monitor._strip_lead_ack(["好，我明白了。", "我會直接猜。"])[0], ["我會直接猜。"])
        self.assertEqual(monitor._strip_lead_ack(["喔，好！", "抱歉！", "我沒抓到你的感覺。"])[0], ["我沒抓到你的感覺。"])

    def test_never_strips_to_empty(self):
        self.assertEqual(monitor._strip_lead_ack(["好。"])[0], ["好。"])
        self.assertEqual(monitor._strip_lead_ack(["嗯。", "好。"])[0], ["好。"])

    def test_caps_at_two(self):
        out, n = monitor._strip_lead_ack(["嗯。", "好。", "喔。", "真正的內容在這裡。"])
        self.assertEqual((n, out), (2, ["喔。", "真正的內容在這裡。"]))

    def test_middle_and_tail_untouched(self):
        b = ["我想了一下。", "好。", "嗯。"]
        self.assertEqual(monitor._strip_lead_ack(b)[0], b)

    def test_answer_bubble_is_never_stripped(self):
        b = ["那我猜，你是......處女座？"]
        self.assertEqual(monitor._strip_lead_ack(b)[0], b)


class SayWiringTest(unittest.TestCase):
    class Cl:
        def __init__(self):
            self.sent, self.dry_run = [], False

        def send(self, t):
            self.sent.append(t)
            return True

    def _out(self, text, on):
        monitor._TURN.clear()
        monitor._TURN.update({"bubbles": None, "act_first": on})
        cl = self.Cl()
        monitor._say(cl, text)
        return cl.sent

    def test_flag_on_drops_the_filler(self):
        sent = self._out("好，我明白了。我會直接猜。那我猜，你是處女座？", True)
        self.assertNotIn("好，我明白了。", sent)
        self.assertTrue(any("處女座" in x for x in sent))

    def test_flag_off_is_unchanged(self):
        sent = self._out("好，我明白了。我會直接猜。那我猜，你是處女座？", False)
        self.assertIn("好，我明白了。", sent)

    def test_proactive_paths_untouched(self):
        # 主動出聲走 prefix/state，不在互動出口的這道剝除裡（arm 只在 handle_message）
        src = io.open("telegram_monitor/monitor.py", encoding="utf-8").read()
        self.assertIn('_TURN["act_first"] = getattr(cfg, "act_first_enabled", False)', src)


class ConfigTest(unittest.TestCase):
    def test_flag_synced(self):
        src = io.open("telegram_monitor/config.py", encoding="utf-8").read()
        self.assertIn("ACT_FIRST", src)
        self.assertIn("act_first_enabled", src)
        self.assertRegex(io.open(".env.example", encoding="utf-8").read(), re.compile(r"^ACT_FIRST=1", re.M))
        self.assertIn("ACT_FIRST", io.open("README.md", encoding="utf-8").read())
        self.assertFalse(getattr(SimpleNamespace(), "act_first_enabled", False))


if __name__ == "__main__":
    unittest.main()
