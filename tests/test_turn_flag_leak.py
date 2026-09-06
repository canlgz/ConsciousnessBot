# -*- coding: utf-8 -*-
"""🩹 §2.14 通盤複查抓到的三個破綻——全是同一族：互動限定的 `_TURN` 旗沒鎖分支、沒人清。

§1.85 早記過 replay_guard 的殘值病（「在每個互動輪被設 True 卻全檔沒有任何 pop ⇒ 殘留洩進之後
所有主動出口」），§2.09/§2.10/§2.13 又犯了一次。最壞情境（本檔第一組測試釘死）：
🤝 守約兌現句是短句且 180 秒內同字 ⇒ 被 §2.13 吞掉、`_say` 回 True ⇒ **記成已兌現但什麼都沒送**
＝把 §1.85 整套交付舉證白修。全 stub、零網路。
"""

import io
import os
import tempfile
import unittest

from telegram_monitor import monitor
from telegram_monitor.state import State


class Cl:
    def __init__(self):
        self.sent, self.dry_run = [], False

    def send(self, t):
        self.sent.append(t)
        return True


def _state():
    return State(os.path.join(tempfile.mkdtemp(), "s.json"))


class ShortDupStaysInteractiveTest(unittest.TestCase):
    def setUp(self):
        monitor._SHORT_SENT.clear()

    def test_delivery_short_line_is_never_swallowed(self):
        # ★ 破綻本體：互動送過「嗨，我來了。」→ 180 秒內 🤝 兌現同一句 → 修前被吞掉還回 True＝假兌現
        cl = Cl()
        monitor._TURN.clear()
        monitor._TURN.update({"bubbles": None, "short_dup_guard": True})
        monitor._say(cl, "嗨，我來了。")
        monitor._TURN.clear()
        monitor._TURN.update({"bubbles": None, "short_dup_guard": True})
        ok = monitor._say(cl, "嗨，我來了。", prefix="🤝 ", state=_state())
        self.assertTrue(ok)
        self.assertEqual(cl.sent[-1], "🤝 嗨，我來了。")           # 真的送出去了

    def test_interactive_dup_is_still_blocked(self):
        cl = Cl()
        for _ in range(3):
            monitor._TURN.clear()
            monitor._TURN.update({"bubbles": None, "short_dup_guard": True})
            monitor._say(cl, "好，記下來了。")
        self.assertEqual(cl.sent, ["好，記下來了。"])               # §2.13 的原修法不受影響


class ActFirstStaysInteractiveTest(unittest.TestCase):
    def test_proactive_lead_ack_is_untouched(self):
        # 殘值洩漏：act_first 是上一輪互動 arm 的，主動出聲（帶 prefix/state）不得被剝開頭
        cl = Cl()
        monitor._TURN.clear()
        monitor._TURN.update({"bubbles": None, "act_first": True})
        monitor._say(cl, "好。今天周遭很靜，我把心跳放慢了。", prefix="🍃 ", state=_state(), topic="環境換檔")
        self.assertTrue(cl.sent[0].startswith("🍃 好。"))          # 開頭的「好。」還在

    def test_interactive_strip_still_works(self):
        cl = Cl()
        monitor._TURN.clear()
        monitor._TURN.update({"bubbles": None, "act_first": True})
        monitor._say(cl, "好，我明白了。我會直接猜。")
        self.assertNotIn("好，我明白了。", cl.sent)


class MoodLineStaysInteractiveTest(unittest.TestCase):
    LINE = "（我此刻的座標是 V +0.10、A +0.05——落在「平穩」那一帶。）"

    def test_leftover_never_leaks_into_proactive(self):
        # 殘值洩漏：互動輪沒消化掉的座標補救行，不得被塞進之後的主動訊息
        cl = Cl()
        monitor._TURN.clear()
        monitor._TURN.update({"bubbles": None, "mood_data_line": self.LINE})
        monitor._say(cl, "今天周遭很靜。", prefix="🫧 ", state=_state(), topic="x")
        self.assertFalse(any("座標" in x for x in cl.sent))

    def test_interactive_append_still_works(self):
        cl = Cl()
        monitor._TURN.clear()
        monitor._TURN.update({"bubbles": None, "mood_data_line": self.LINE})
        monitor._say(cl, "我現在的感覺，嗯，是比較興奮、雀躍的。")
        self.assertTrue(any("V +0.10" in x for x in cl.sent))


class HistorySwapSurvivesTransformsTest(unittest.TestCase):
    def test_markdown_reply_is_still_swapped(self):
        # ★ 破綻：舊登記鍵取在 strip_markdown **之後**，而 _remember 收到的是原字串 ⇒ 帶粗體的回覆整個替換靜默失效
        s = _state()
        t = "**好，我明白了。**我會直接猜。那我猜，你是處女座？"
        cl = Cl()
        monitor._TURN.clear()
        monitor._TURN.update({"bubbles": None, "act_first": True})
        monitor._say(cl, t)
        monitor._remember(s, "model", t)
        self.assertEqual(s.convo_history[-1]["text"], "".join(cl.sent))
        self.assertNotIn("我明白了", s.convo_history[-1]["text"])

    def test_multiple_says_per_turn_each_registered(self):
        # dict 化之後：同一輪兩次 _say 都剝過，兩筆歷史各自換成真送出的版本
        s = _state()
        monitor._TURN.clear()
        monitor._TURN.update({"bubbles": None, "act_first": True})
        cl = Cl()
        a, b = "好，我明白了。我先看看那條線。", "嗯。它停在留白那一筆。"
        monitor._say(cl, a)
        monitor._say(cl, b)
        monitor._remember(s, "model", a)
        monitor._remember(s, "model", b)
        self.assertEqual(s.convo_history[-2]["text"], "我先看看那條線。")
        self.assertEqual(s.convo_history[-1]["text"], "它停在留白那一筆。")


class PerceiveClearsTest(unittest.TestCase):
    def test_flags_are_cleared_before_the_feel_phase(self):
        # 皮帶加吊帶：旗標混開時仍走裸 _say 的主動 lane 也不吃上一輪互動的殘值
        src = io.open("telegram_monitor/monitor.py", encoding="utf-8").read()
        i = src.rindex('_TURN["ground_now"] = None')
        seg = src[i:i + 600]
        for key in ("act_first", "act_first_sent", "short_dup_guard", "mood_data_line"):
            self.assertIn(f'_TURN.pop("{key}", None)', seg, key)


if __name__ == "__main__":
    unittest.main()
