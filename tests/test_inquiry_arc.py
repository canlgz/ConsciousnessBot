"""🌱 探究弧（inquiry arc）：與路由軸/違常軸正交的第三軸——從對話累積讀「發現→疑惑→因為→所以→然後→原來如此」
走到哪、軌跡形狀（卡住/膚淺跳階/退回/健康弧）。階段是弱訊號、弧從累積湧現；旗標關＝detect_stage 恆 None。"""

import unittest
from types import SimpleNamespace

from telegram_monitor import inquiry_arc, persona


class DetectStageTest(unittest.TestCase):
    def test_positives(self):
        cases = [
            ("我發現我每次拖延前都會先滑手機", "notice"),
            ("欸我注意到一個現象，週末記的東西特別多", "notice"),
            ("才發現原來有兩條線在講同一件事", "notice"),
            ("可是我想不通為什麼會這樣", "puzzle"),
            ("我卡住了，不知道該怎麼接", "puzzle"),
            ("到底為什麼每次都卡在同一個地方？", "puzzle"),
            ("我覺得原因是我太晚開始", "because"),
            ("之所以拖延，是因為我怕做不好", "because"),     # 「之所以」含『所以』子字串但語意是 because
            ("問題出在我沒有先拆小步驟", "because"),
            ("所以我大概知道該怎麼調整了", "so"),
            ("也就是說重點不是時間而是環境", "so"),
            ("接下來我想試試番茄鐘", "next"),
            ("下一步我打算每天記一筆", "next"),
            ("原來如此，難怪我每次都這樣", "aha"),
            ("喔我懂了，原來是順序的問題", "aha"),
            ("終於想通了，原來關鍵在這", "aha"),
        ]
        for t, exp in cases:
            self.assertEqual(inquiry_arc.detect_stage(t), exp, t)

    def test_pure_question_is_puzzle_but_with_cause_conn_is_because(self):
        self.assertEqual(inquiry_arc.detect_stage("為什麼我每次都失敗？"), "puzzle")
        self.assertEqual(inquiry_arc.detect_stage("是不是因為我方法錯了？"), "because")

    def test_yields_to_farewell_backchannel_greeting(self):
        self.assertIsNone(inquiry_arc.detect_stage("懂了，那我先去忙囉", is_farewell=True))   # 讓給 closing_kind('leaving')
        self.assertIsNone(inquiry_arc.detect_stage("是啊", is_backchannel=True))
        self.assertIsNone(inquiry_arc.detect_stage("早安", is_greeting=True))

    def test_long_understanding_plus_leaving_not_aha(self):
        # 對抗驗證 high：長句『理解＋道別』is_farewell 受 len<=12 漏判 → 本軸自有 _LEAVING 兜底讓位、不在告別句回探
        for t in ("原來如此啊謝謝你跟我講這麼多那我先去忙囉",
                  "好啦我懂了那我先去睡覺囉晚安",
                  "懂了懂了先這樣囉我去忙別的了"):
            self.assertIsNone(inquiry_arc.detect_stage(t), t)   # is_farewell 預設 False 仍 None
        # 不帶道別的頓悟仍標 aha（現狀破口、要接住）
        self.assertEqual(inquiry_arc.detect_stage("原來如此，難怪我每次都這樣"), "aha")

    def test_followup_then_ne_not_next(self):
        self.assertIsNone(inquiry_arc.detect_stage("然後呢？"))     # 純催對方講＝followup，非自己行動
        self.assertIsNone(inquiry_arc.detect_stage("再來呢"))

    def test_neutral_chitchat_is_none(self):
        for t in ("今天午餐吃什麼好", "幫我列一下這週的記寫", "現在幾點", "你還好嗎"):
            self.assertIsNone(inquiry_arc.detect_stage(t), t)

    def test_flag_off_always_none(self):
        for t in ("我發現一個現象", "原來如此，難怪", "所以結論是這樣"):
            self.assertIsNone(inquiry_arc.detect_stage(t, enabled=False), t)


class ArcTrajectoryTest(unittest.TestCase):
    def _log(self, *stages):
        return [{"stage": s, "ts": i} for i, s in enumerate(stages)]

    def test_full_healthy_arc_resolved(self):
        tr = inquiry_arc.arc_trajectory(self._log("notice", "puzzle", "because", "aha"))
        self.assertTrue(tr["advancing"] and tr["resolved"])
        self.assertEqual(tr["depth"], 3)
        self.assertEqual(tr["shape"], "healthy")

    def test_single_stage_not_a_arc(self):
        tr = inquiry_arc.arc_trajectory(self._log("notice"))
        self.assertFalse(tr["advancing"])
        self.assertEqual(tr["depth"], 0)
        self.assertIsNone(tr["shape"])

    def test_lone_aha_no_arc(self):
        # 場景三：沒過程的收束 → 不腦補歷程感（advancing False、shape None）
        tr = inquiry_arc.arc_trajectory(self._log("aha"))
        self.assertFalse(tr["advancing"])
        self.assertIsNone(tr["shape"])

    def test_shapes(self):
        cases = [
            (("notice", "puzzle", "because", "so", "next"), "healthy"),
            (("notice", "puzzle", "puzzle", "puzzle"), "stalled"),
            (("notice", "aha"), "shallow_jump"),
            (("notice", "because", "so", "puzzle"), "retreat"),
            (("because", "next", "next"), "next_loop"),
        ]
        for seq, exp in cases:
            self.assertEqual(inquiry_arc.arc_trajectory(self._log(*seq))["shape"], exp, seq)

    def test_empty(self):
        tr = inquiry_arc.arc_trajectory([])
        self.assertEqual(tr["depth"], 0)
        self.assertIsNone(tr["latest"])


class InquiryHintTest(unittest.TestCase):
    def test_no_hint_below_depth(self):
        self.assertEqual(persona.inquiry_stage_hint({"depth": 0, "shape": None, "latest": "notice"}), "")
        self.assertEqual(persona.inquiry_stage_hint(None), "")

    def test_stalled_hint(self):
        h = persona.inquiry_stage_hint({"depth": 1, "shape": "stalled", "latest": "puzzle", "resolved": False})
        self.assertIn("卡在", h)
        self.assertIn("別急著", h)

    def test_resolved_hint_history_sense(self):
        h = persona.inquiry_stage_hint({"depth": 3, "shape": "healthy", "latest": "aha", "resolved": True})
        self.assertIn("想通", h)

    def test_healthy_next_move(self):
        h = persona.inquiry_stage_hint({"depth": 1, "shape": "healthy", "latest": "next", "resolved": False})
        self.assertIn("學習脈絡", h)

    def test_hint_never_leaks_stage_labels(self):
        # 不可把內部階段英文/標籤名洩漏給 LLM 當輸出
        for shape, latest in (("stalled", "puzzle"), ("healthy", "because"), ("shallow_jump", "aha")):
            h = persona.inquiry_stage_hint({"depth": 2, "shape": shape, "latest": latest, "resolved": False})
            for label in ("notice", "puzzle", "because", "so", "next", "aha", "stalled", "shallow_jump"):
                self.assertNotIn(label, h)


class ObserveInquiryArcTest(unittest.TestCase):
    """monitor._observe_inquiry_arc 的副作用：逐行記階段、不每則閒聊覆寫 intent_reading、近窗過濾。"""
    def _cfg(self):
        return SimpleNamespace(inquiry_arc_enabled=True, intent_log_max=20, inquiry_arc_window_sec=3600)

    def test_burst_per_line_records_micro_arc(self):
        from telegram_monitor import monitor
        st = SimpleNamespace(user_model=None, intent_reading=None)
        monitor._observe_inquiry_arc(st, "我發現一個問題\n為什麼會這樣\n所以該怎麼辦", 1000, self._cfg())
        log = (st.user_model or {}).get("inquiry_log")
        self.assertEqual([e["stage"] for e in log], ["notice", "puzzle", "so"])   # 微弧逐行保留、不壓成單一 so

    def test_no_stage_does_not_clobber_intent_reading(self):
        from telegram_monitor import monitor
        st = SimpleNamespace(user_model=None, intent_reading="SENTINEL")
        monitor._observe_inquiry_arc(st, "今天天氣不錯", 1000, self._cfg())
        self.assertEqual(st.intent_reading, "SENTINEL")              # 無階段、無弧 → 不碰 state
        self.assertIsNone((st.user_model or {}).get("inquiry_log"))

    def test_window_excludes_stale_stage(self):
        from telegram_monitor import monitor
        st = SimpleNamespace(user_model={"inquiry_log": [{"stage": "notice", "ts": 0}]}, intent_reading=None)
        # 這則 puzzle 在 now=99999；舊 notice(ts=0) 超出 window → 弧只剩這則 puzzle、depth<2、不注入
        h = monitor._observe_inquiry_arc(st, "可是我想不通為什麼", 99999, self._cfg())
        self.assertEqual(h, "")


if __name__ == "__main__":
    unittest.main()
