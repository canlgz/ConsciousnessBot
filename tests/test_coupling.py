"""對話耦合（觀測層）：bot↔使用者兩個意向性的耦合動力學——觸發、衰減、收尾品質、均衡歸零。

本階段純觀測（只算狀態、不改對外行為），所以測的是純動力學是否如設計跑。
"""

import unittest

from telegram_monitor import coupling

T0 = 1_700_000_000
MIN = 60


class EstimateUserIntentTest(unittest.TestCase):
    def test_substance_and_brevity(self):
        self.assertGreater(coupling.estimate_user_intent("我覺得這個想法其實滿有意思的，想多聊聊"), 0.7)  # 回得實↑
        self.assertLess(coupling.estimate_user_intent("嗯"), 0.5)                                       # 太短↓

    def test_ball_back_and_closing(self):
        self.assertGreater(coupling.estimate_user_intent("你覺得呢？"), 0.7)        # 把球丟回來＝再投資↑
        self.assertLess(coupling.estimate_user_intent("我懂了，謝謝"), 0.2)         # 收尾語＝主動歸零↓

    def test_latency(self):
        fast = coupling.estimate_user_intent("好喔好喔好喔", latency_s=10)
        slow = coupling.estimate_user_intent("好喔好喔好喔", latency_s=60 * MIN)
        self.assertGreater(fast, slow)                                            # 秒回 > 拖很久


class CouplingDynamicsTest(unittest.TestCase):
    def test_engage_triggers_bot_and_opens_round(self):
        c = coupling.Coupling()
        coupling.engage(c, "欸我跟你說一件事", T0)
        self.assertTrue(c.round_open)
        self.assertAlmostEqual(c.i_bot, 0.9, places=2)          # 由使用者觸發
        self.assertGreater(c.i_user, 0.5)

    def test_decays_when_ignored_then_closes_as_ignored(self):
        c = coupling.Coupling()
        coupling.engage(c, "嗯", T0)                             # 低投入：i_user 偏低、無收尾語
        coupling.observe(c, T0 + 5 * MIN)
        self.assertTrue(c.round_open)                           # 才 5 分，I_bot 還撐著
        self.assertLess(c.i_bot, 0.9)                           # 但已在遞減
        coupling.observe(c, T0 + 40 * MIN)                      # 被晾久了
        self.assertFalse(c.round_open)                          # 雙方皆→0 → 收掉
        self.assertEqual(c.last_closure, "ignored")            # 沒收尾語、線就淡掉＝被晾

    def test_understanding_closure(self):
        c = coupling.Coupling()
        coupling.engage(c, "喔喔我懂了，原來如此", T0)            # 理解性收尾訊號
        coupling.observe(c, T0 + 40 * MIN)
        self.assertFalse(c.round_open)
        self.assertEqual(c.last_closure, "understanding")

    def test_leaving_closure(self):
        c = coupling.Coupling()
        coupling.engage(c, "我先去忙了，改天再聊", T0)
        coupling.observe(c, T0 + 40 * MIN)
        self.assertEqual(c.last_closure, "leaving")

    def test_closure_marks_just_closed_and_mood_delta(self):
        # A1 收尾品質染情緒：observe 收掉一輪時標 just_closed（供呼叫端染一次）；被晾↓、理解性↑、離開中性
        c = coupling.Coupling()
        coupling.engage(c, "嗯", T0)
        coupling.observe(c, T0 + 40 * MIN)
        self.assertEqual(c.just_closed, "ignored")
        self.assertLess(coupling.closure_mood_delta("ignored"), 0)            # 被晾＝失落
        self.assertGreater(coupling.closure_mood_delta("understanding"), 0)   # 理解性收尾＝暖
        self.assertEqual(coupling.closure_mood_delta("leaving"), 0)           # 離開＝中性

    def test_reengage_reopens_a_new_round(self):
        c = coupling.Coupling()
        coupling.engage(c, "嗯", T0)
        coupling.observe(c, T0 + 40 * MIN)
        self.assertFalse(c.round_open)
        coupling.engage(c, "欸我回來了", T0 + 60 * MIN)           # 久別重逢→開新一輪
        self.assertTrue(c.round_open)
        self.assertAlmostEqual(c.opened_ts, T0 + 60 * MIN, places=0)

    def test_quick_back_and_forth_stays_alive(self):
        c = coupling.Coupling()
        coupling.engage(c, "我覺得這條線其實還沒收完", T0)
        for k in range(1, 6):                                   # 每 2 分鐘、投入地來回＝活對話
            coupling.engage(c, "對啊，而且我又想到另一個有意思的點", T0 + k * 2 * MIN, latency_s=2 * MIN)
        self.assertTrue(c.round_open)
        self.assertGreater(c.i_bot, 0.6)                       # 投入的再回應讓 I_bot 維持高

    def test_perfunctory_replies_converge_to_zero(self):
        # 結束關鍵②：一連串敷衍的再回應（嗯/喔）不再把 I_bot 拉回高點 → 收斂到 0、這輪自然收掉
        c = coupling.Coupling()
        coupling.engage(c, "欸我跟你說一個想法，滿有意思的", T0)
        high = c.i_bot
        for k in range(1, 7):
            coupling.engage(c, "嗯", T0 + k * 3 * MIN, latency_s=3 * MIN)
        self.assertLess(c.i_bot, high)                         # 敷衍沒把 I_bot 一直拉回高點（不再 max-slam）
        coupling.observe(c, T0 + 30 * MIN)
        self.assertFalse(c.round_open)                         # 趨近 0 → 收掉（不被「嗯」無限續命）
        self.assertEqual(c.last_closure, "ignored")

    def test_engaged_reply_raises_more_than_perfunctory(self):
        # 「再回應增或減 bot 意向性」：投入的回應 > 敷衍的回應 對 I_bot 的影響
        sub = coupling.Coupling(); coupling.engage(sub, "你好啊", T0)
        per = coupling.Coupling(); coupling.engage(per, "你好啊", T0)
        coupling.engage(sub, "我覺得這點其實還可以再延伸下去，你怎麼看？", T0 + 5 * MIN, latency_s=MIN)
        coupling.engage(per, "喔", T0 + 5 * MIN, latency_s=MIN)
        self.assertGreater(sub.i_bot, per.i_bot)

    def test_status_line_observes_and_formats(self):
        c = coupling.Coupling()
        coupling.engage(c, "在嗎", T0)
        line = coupling.status_line(c, T0 + MIN)
        self.assertIn("I_bot=", line)
        self.assertIn("Î_user=", line)
        self.assertIn("這輪活著", line)


class LiveRoundGateTest(unittest.TestCase):
    """🔗 因應 alive 第一步：活著時 bot 壓住自己的雜訊——_in_live_round 是那道閘。"""

    def test_in_live_round_helper(self):
        from types import SimpleNamespace
        from telegram_monitor import monitor
        self.assertFalse(monitor._in_live_round(SimpleNamespace(coupling=None)))
        self.assertFalse(monitor._in_live_round(SimpleNamespace(coupling=coupling.Coupling())))         # 這輪沒開
        self.assertTrue(monitor._in_live_round(SimpleNamespace(coupling=coupling.Coupling(round_open=True))))


if __name__ == "__main__":
    unittest.main()
