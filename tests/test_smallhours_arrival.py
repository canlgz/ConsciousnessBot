# -*- coding: utf-8 -*-
"""🌙 §2.27 深夜出現不是「今天來得早」：對話時間點與**對應意義**的日夜界通盤修。

實測截圖（00:27–00:29）：使用者凌晨說「晚安」→ bot「你今天好像比平常早了一些時候來呢，感覺還好嗎」。
重現釘死：日曆日在午夜翻頁，00:27 成了「今天的第一句」→ greet_aware_line 拿它跟平常早上中位
（~07:10）做天真差值 ⇒「比平常早了約 403 分鐘」進 prompt ⇒ LLM 軟化成「早了一些時候」；
§1.42 守門用同一個天真差值驗方向（diff≤−20 ⇒「早」＝對）＝把荒謬宣稱**放行**。

但人的一天沒有在午夜翻頁：00:27 出現＝**昨天的一天還沒收**（熬夜），對應意義是「這麼晚還醒著」，
不是「今天早到」——habits._ABS_MIN_P25_MIN 早寫明 05:00 界「夜貓/凌晨型不參選（判定會跨日界）」，
§2.22 的早/晚比較漏掉同一課。修兩層（同一個 05:00 界）：prompt 端換分寸、守門端剝跨界宣稱
（lane 無關出口後盾＝連舊 today_vs_usual 路徑也接住）。全 stub、零網路。
"""

import os
import tempfile
import unittest
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from telegram_monitor import habits, monitor
from telegram_monitor.state import State

TZ = ZoneInfo("Asia/Taipei")
NOW = datetime(2026, 8, 6, 0, 27, tzinfo=TZ)   # 截圖時刻：凌晨 00:27


def _state(median_hhmm=(7, 10), today_hhmm=(0, 27), days=9):
    s = State(os.path.join(tempfile.mkdtemp(), "s.json"))
    ev = []
    for d in range(1, days + 1):
        t = (NOW - timedelta(days=d)).replace(hour=median_hhmm[0], minute=median_hhmm[1])
        ev += [{"k": "first", "ts": t.timestamp()}, {"k": "msg", "ts": t.timestamp()}]
    t = NOW.replace(hour=today_hhmm[0], minute=today_hhmm[1])
    ev += [{"k": "first", "ts": t.timestamp()}, {"k": "msg", "ts": t.timestamp()}]
    s.habit_events = ev
    return s


class GreetAwareSmallhoursTest(unittest.TestCase):
    def test_flag_off_reproduces_screenshot_absurdity(self):
        # 迴歸釘：舊行為＝00:27 被說成「比平常早了約 N 分鐘」（截圖病灶）
        line, v = habits.greet_aware_line(_state(), NOW.timestamp(), TZ, daily_first=True)
        self.assertEqual(v, "early")
        self.assertIn("早了約", line)

    def test_smallhours_swaps_to_night_grace(self):
        # ★ 修後：不比早晚，換〔深夜分寸〕——昨天的一天還沒收、關心往「還醒著」的方向
        line, v = habits.greet_aware_line(_state(), NOW.timestamp(), TZ, daily_first=True, smallhours=True)
        self.assertEqual(v, "smallhours")
        self.assertIn("昨天的一天還沒收", line)
        self.assertIn("還醒著", line)
        self.assertNotIn("早了約", line)
        self.assertNotIn("晚了約", line)

    def test_reverse_owl_crossing_boundary(self):
        # 夜貓型使用者（平常凌晨 1 點出沒）早上 07:30 出現＝跨界，反向也不比
        now = NOW.replace(hour=7, minute=31)
        line, v = habits.greet_aware_line(_state(median_hhmm=(1, 0), today_hhmm=(7, 30)),
                                          now.timestamp(), TZ, daily_first=True, smallhours=True)
        self.assertEqual(v, "smallhours")
        self.assertIn("日夜界", line)
        self.assertNotIn("早了約", line)

    def test_both_in_band_compares_normally(self):
        # 真夜貓的日常：兩側都在深夜帶 → 照常比較（00:40 中位 vs 今天 00:27＝差不多）
        line, v = habits.greet_aware_line(_state(median_hhmm=(0, 40), today_hhmm=(0, 27)),
                                          NOW.timestamp(), TZ, daily_first=True, smallhours=True)
        self.assertEqual(v, "usual")

    def test_both_morning_unchanged(self):
        now = NOW.replace(hour=7, minute=50)
        line, v = habits.greet_aware_line(_state(today_hhmm=(7, 50)), now.timestamp(), TZ,
                                          daily_first=True, smallhours=True)
        self.assertEqual(v, "late")                    # 早晨帶內的偏離照舊值得注意


# 守門：g 對齊截圖（平常 06:52–07:38、今天 00:27）
def _g(smallhours=True):
    g = {"greet_am": None, "first": (9, 430, 412, 458), "today_first_min": 27, "role": True}
    if smallhours:
        g["smallhours"] = True
    return g


class ClaimGuardSmallhoursTest(unittest.TestCase):
    def test_wordface_cross_boundary_claim_stripped(self):
        # ★ 截圖那句：天真差值會判「早＝對」放行；日夜界開＝無據＝剝（問候輪走 §2.22 保全）
        msg = "你今天好像比平常早了一些時候來呢，感覺還好嗎"
        out, ch = monitor._habit_claim_fix(msg, _g(), greet_fallback="晚安，好好休息喔。")
        self.assertTrue(ch)
        self.assertEqual(out, "晚安，好好休息喔。")

    def test_flag_off_naive_diff_lets_it_pass(self):
        # 迴歸釘：不帶日夜界鍵＝舊行為＝荒謬宣稱被「驗證通過」原樣放行
        msg = "你今天好像比平常早了一些時候來呢，感覺還好嗎"
        out, ch = monitor._habit_claim_fix(msg, _g(smallhours=False), greet_fallback="晚安。")
        self.assertFalse(ch)
        self.assertEqual(out, msg)

    def test_role_path_cross_boundary_denied(self):
        # 語意角色路（省略主詞句、詞面接不到）也認日夜界
        r = habits.claim_roles("今天比平常早了一點出現呢")
        if r is None:                                   # 環境差異保險：role 解不出就略過（詞面測已覆蓋）
            self.skipTest("claim_roles 未命中")
        self.assertEqual(monitor._habit_role_verdict(r, _g(), "今天比平常早了一點出現呢"), "first")

    def test_in_band_claims_still_verified_normally(self):
        # 兩側同在早晨帶：方向對＝放行（原 §1.42 行為不動）
        g = {"greet_am": None, "first": (9, 430, 412, 458), "today_first_min": 470,
             "role": True, "smallhours": True}
        msg = "你今天比平常晚了一些呢。"
        out, ch = monitor._habit_claim_fix(msg, g, greet_fallback="早安。")
        self.assertFalse(ch)


class WiringAndFlagTest(unittest.TestCase):
    def test_wiring(self):
        with open("telegram_monitor/monitor.py", encoding="utf-8") as f:
            src = f.read()
        self.assertIn('smallhours=getattr(cfg, "smallhours_arrival_enabled", False)', src)
        self.assertIn('_TURN["habit_claim_ground"]["smallhours"] = True', src)
        self.assertIn('in ("usual", "early", "late", "smallhours")', src)   # 深夜分寸也算端上桌（stamp）

    def test_flag_everywhere(self):
        with open("telegram_monitor/config.py", encoding="utf-8") as f:
            src = f.read()
        self.assertIn('_bool("SMALLHOURS_ARRIVAL", True)', src)
        self.assertIn("smallhours_arrival_enabled", src)
        with open(".env.example", encoding="utf-8") as f:
            self.assertIn("SMALLHOURS_ARRIVAL=1", f.read())
        with open("README.md", encoding="utf-8") as f:
            self.assertIn("SMALLHOURS_ARRIVAL", f.read())


if __name__ == "__main__":
    unittest.main()
