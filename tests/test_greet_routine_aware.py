# -*- coding: utf-8 -*-
"""🕘 §2.22 問候的有意識作息覺察：「早安」不再換來一張統計報表。

實測截圖 06:58：使用者「早安」→ bot 回「我手上記到的是：你一天跟我說上第一句話大多在 06:52 到
07:38 之間，我也才看到 9 次，可能只是我剛好看到的都那樣」＝拿 §1.42 守門的統計模板回問候。
使用者定調：回應太制式化；要能觀察/偵測/發現作息與習慣、**有意識地互動回應**。

根因鏈（非猜）：greeting lane 注入 today_vs_usual_line 的**報表框架**（中位/樣本/「只准照這條」）、
卻沒配 §1.96 的講法守則（ROUTINE_VOICE_HINT 只掛五條事實卡 lane、voice_greeting 從來沒有）⇒
與 greeting.facts 的「別報數據」打架 ⇒ LLM 照唸報表或換句話講錯方向（06:58 vs 中位差 <20 分＝
「差不多」、被說成「早」）⇒ §1.42/§1.97 守門剝光 ⇒ 兜底＝固定統計模板獨走＝報表回「早安」。

修三層：greet_aware_line 熟悉感框架（偏離 ≥20 分才特別提；尋常日子近 40h 提過就不注入＝結構性
保證不每天複誦）＋ state.greet_routine_ts（跨重生）＋守門 greet_fallback（問候輪剝錯句不補統計句、
剝光退回問候模板）。全 stub、零網路。
"""

import json
import os
import re
import tempfile
import unittest
from datetime import datetime, timedelta
from types import SimpleNamespace
from zoneinfo import ZoneInfo

from telegram_monitor import habits, monitor
from telegram_monitor.state import State

TZ = ZoneInfo("Asia/Taipei")
NOW = datetime(2026, 8, 1, 6, 58, tzinfo=TZ)     # 截圖的早晨：06:58 說早安
NOW_TS = NOW.timestamp()
GAP = habits._GREET_REMARK_GAP_S                  # 尋常觀察的再提冷卻（40h）
# 覺察行不准把統計**當數據**端出來（「別用中位數…」的禁令本身出現那兩個字無妨；有數字跟著才是報表）
_REPORT_DATA_RE = re.compile(r"中位 ?[\d０-９]|樣本 ?[\d０-９]|[\d０-９]+ ?次")


def _state(first_hhmm=(7, 10), days=9, today=None):
    """近 days 天每天一筆 first（07:10 左右）＋（可選）今天一筆＝有可靠統計的狀態。"""
    s = State(os.path.join(tempfile.mkdtemp(), "s.json"))
    ev = []
    for d in range(1, days + 1):
        t = (NOW - timedelta(days=d)).replace(hour=first_hhmm[0], minute=first_hhmm[1])
        ev.append({"k": "first", "ts": t.timestamp()})
        ev.append({"k": "msg", "ts": t.timestamp()})
    if today is not None:
        t = NOW.replace(hour=today[0], minute=today[1])
        ev.append({"k": "first", "ts": t.timestamp()})
        ev.append({"k": "msg", "ts": t.timestamp()})
    s.habit_events = ev
    return s


class GreetAwareLineTest(unittest.TestCase):
    def test_usual_first_time_offers_familiarity(self):
        # 差 <20 分＝尋常日子；沒提過 → 熟悉感結論（差不多），禁「早/晚」方向、禁報表詞
        line, v = habits.greet_aware_line(_state(today=(6, 58)), NOW_TS, TZ)
        self.assertEqual(v, "usual")
        self.assertIn("差不多", line)
        self.assertIn("06:58", line)                       # 今天的時刻有接地
        self.assertIn("別**說他比平常早或晚"[1:], line)     # 明令禁方向詞
        self.assertNotRegex(line, _REPORT_DATA_RE)          # 統計不當數據端出來
        self.assertNotIn("07:10", line)                     # 平常的中位/區間時刻不外洩（只有今天的時刻）

    def test_usual_within_cooldown_stays_quiet(self):
        # ★ 截圖病根之一的結構解：昨天才提過「差不多時間」→ 今天**不注入**＝好好回應問候本身
        line, v = habits.greet_aware_line(_state(today=(6, 58)), NOW_TS, TZ,
                                          last_remark_ts=NOW_TS - 12 * 3600)
        self.assertEqual((line, v), ("", "quiet"))

    def test_usual_after_cooldown_speaks_again(self):
        line, v = habits.greet_aware_line(_state(today=(6, 58)), NOW_TS, TZ,
                                          last_remark_ts=NOW_TS - GAP - 3600)
        self.assertEqual(v, "usual")
        self.assertTrue(line)

    def test_deviation_late_is_worth_noticing(self):
        # 偏離 ≥20 分＝值得有意識說出來：方向＋約 N 分鐘（方向詞正是守門驗的東西＝照講必過門）
        line, v = habits.greet_aware_line(_state(today=(7, 50)), NOW.replace(hour=7, minute=51).timestamp(), TZ)
        self.assertEqual(v, "late")
        self.assertRegex(line, r"比平常晚了約 \d+ 分鐘")
        self.assertIn("注意到", line)                       # 熟悉感框架：這是你自己注意到的事
        self.assertNotRegex(line, _REPORT_DATA_RE)          # 統計不當數據端出來
        self.assertNotIn("07:10", line)                     # 平常的中位/區間時刻不外洩

    def test_deviation_early_is_worth_noticing(self):
        line, v = habits.greet_aware_line(_state(today=(6, 20)), NOW_TS, TZ)
        self.assertEqual(v, "early")
        self.assertRegex(line, r"比平常早了約 \d+ 分鐘")

    def test_deviation_within_short_cooldown_stays_quiet(self):
        # 截圖 23:16→隔日 06:49：七小時內不再端第二次同形「比平常早/晚」盤問。
        line, v = habits.greet_aware_line(_state(today=(7, 50)), NOW.replace(hour=7, minute=51).timestamp(), TZ,
                                          last_remark_ts=NOW_TS - 3600)
        self.assertEqual((line, v), ("", "quiet"))

    def test_greeting_kind_uses_its_own_history_and_excludes_today(self):
        s = State(os.path.join(tempfile.mkdtemp(), "s.json"))
        ev = []
        for d, minute in ((1, 10), (2, 12), (3, 8)):
            am = (NOW - timedelta(days=d)).replace(hour=7, minute=minute)
            pm = (NOW - timedelta(days=d)).replace(hour=23, minute=40 + d)
            ev.extend(({"k": "greet_am", "ts": am.timestamp()}, {"k": "greet_pm", "ts": pm.timestamp()}))
        ev.append({"k": "greet_am", "ts": NOW.replace(hour=6, minute=20).timestamp()})  # 本輪先入帳
        s.habit_events = ev
        line, verdict = habits.greet_aware_line(
            s, NOW.replace(hour=6, minute=20).timestamp(), TZ, greet_kind="morning")
        self.assertEqual(verdict, "early")
        self.assertIn("這句早安", line)
        self.assertNotIn("出現", line)
        self.assertIn("比平常早", line)

    def test_today_event_does_not_complete_minimum_sample(self):
        s = State(os.path.join(tempfile.mkdtemp(), "s.json"))
        s.habit_events = [
            {"k": "greet_am", "ts": (NOW - timedelta(days=1)).replace(hour=7, minute=10).timestamp()},
            {"k": "greet_am", "ts": (NOW - timedelta(days=2)).replace(hour=7, minute=12).timestamp()},
            {"k": "greet_am", "ts": NOW.replace(hour=6, minute=20).timestamp()},
        ]
        line, verdict = habits.greet_aware_line(
            s, NOW.replace(hour=6, minute=20).timestamp(), TZ, greet_kind="morning")
        self.assertEqual(verdict, "none")
        self.assertEqual(line, habits._NO_ROUTINE_STAT_LINE)

    def test_repeated_greetings_same_day_count_as_one_day(self):
        ev = []
        for d in (1, 2, 3):
            base = (NOW - timedelta(days=d)).replace(hour=7, minute=10)
            ev.extend({"k": "greet_am", "ts": (base + timedelta(minutes=m)).timestamp()}
                      for m in (0, 5, 20))
        st = habits.daily_kind_stats(ev, "greet_am", TZ, NOW_TS)
        self.assertEqual(st[0], 3)

    def test_night_greeting_does_not_make_linear_routine_comparison(self):
        self.assertEqual(habits.greet_aware_line(_state(today=(22, 0)), NOW.replace(hour=22).timestamp(), TZ,
                                                 greet_kind="night"), ("", "quiet"))

    def test_no_stats_same_guard_line_as_today_vs_usual(self):
        # 沒統計＝與 today_vs_usual_line 逐字同一句分寸警語（別對他作息下判斷）
        s = State(os.path.join(tempfile.mkdtemp(), "s.json"))
        line, v = habits.greet_aware_line(s, NOW_TS, TZ)
        self.assertEqual(v, "none")
        self.assertEqual(line, habits.today_vs_usual_line(s, NOW_TS, TZ))

    def test_daily_first_semantics_supported(self):
        # §1.63 habit_obs_fix 開＝日曆日語意的路徑也走得通（今天 06:58＝差不多）
        line, v = habits.greet_aware_line(_state(today=(6, 58)), NOW_TS, TZ, daily_first=True)
        self.assertEqual(v, "usual")
        self.assertIn("06:58", line)


# 守門的問候保全：g 的統計對齊截圖（p25=06:52、p75=07:38、樣本 9）——舊行為正是吐那句模板
_G = {"greet_am": None, "first": (9, 415, 412, 458), "today_first_min": 418, "role": True}


class GreetFallbackGuardTest(unittest.TestCase):
    def test_strip_without_report_line(self):
        # 問候輪：錯句剝掉、問候句保留、**不**補「我手上記到的是…」
        out, ch = monitor._habit_claim_fix("早安，新的一天。你今天比平常早了一點呢。", dict(_G),
                                           greet_fallback="早安，新的一天又開始了。")
        self.assertTrue(ch)
        self.assertEqual(out, "早安，新的一天。")
        self.assertNotIn("我手上記到的是", out)

    def test_all_stripped_falls_back_to_greeting(self):
        # ★ 截圖場景：整則都是作息宣稱 → 退回問候模板，絕不拿統計句回「早安」
        out, ch = monitor._habit_claim_fix("你今天比平常早了一點呢。", dict(_G),
                                           greet_fallback="早安，新的一天又開始了。")
        self.assertTrue(ch)
        self.assertEqual(out, "早安，新的一天又開始了。")

    def test_grounded_direction_passes_untouched(self):
        # 照 greet_aware_line 給的結論講（方向對＝晚）＝守門放行、問候保全不啟動
        g = dict(_G, today_first_min=470)   # 今天 07:50、中位 06:55 → 晚
        msg = "早安。你今天比平常晚了一些呢，昨晚有睡好嗎？"
        out, ch = monitor._habit_claim_fix(msg, g, greet_fallback="早安。")
        self.assertFalse(ch)
        self.assertEqual(out, msg)

    def test_greeting_specific_guard_does_not_fall_back_to_daily_first(self):
        g = {"greet_am": (3, 430, 425, 435), "first": (9, 780, 760, 800),
             "today_first_min": 780, "greet_ref": "greet_am", "greet_today_min": 380, "role": True}
        correct = "早安。你今天比平常早了一些。"
        self.assertEqual(monitor._habit_claim_fix(correct, g, greet_fallback="早安。"), (correct, False))
        wrong, changed = monitor._habit_claim_fix("早安。你今天比平常晚了一些。", g, greet_fallback="早安。")
        self.assertTrue(changed)
        self.assertEqual(wrong, "早安。")

    def test_forbidden_greeting_domain_strips_comparison(self):
        g = {"first": (9, 420, 400, 440), "today_first_min": 420,
             "greet_ref": "greet_forbidden", "greet_forbidden": None,
             "greet_today_min": 1260, "role": True}
        out, changed = monitor._habit_claim_fix("你今晚比平常晚很多。", g, greet_fallback="晚上好，我在。")
        self.assertTrue(changed)
        self.assertEqual(out, "晚上好，我在。")

    def test_non_greeting_turn_byte_identical(self):
        # 迴歸釘：不帶 greet_fallback（非問候輪）＝原行為＝補統計誠實句（截圖那句的來源）
        out, ch = monitor._habit_claim_fix("你今天比平常早了一點呢。", dict(_G))
        self.assertTrue(ch)
        self.assertIn("我手上記到的是", out)
        self.assertIn("06:52", out)
        self.assertIn("07:38", out)


class WiringTest(unittest.TestCase):
    def setUp(self):
        with open("telegram_monitor/monitor.py", encoding="utf-8") as f:
            self.src = f.read()

    def test_greeting_lane_swaps_injection_under_flag(self):
        # greeting lane：旗標開走 greet_aware_line＋stash 問候保全；關＝原 today_vs_usual_line
        lane = self.src[self.src.index('if route.kind == "greeting":'):]
        lane = lane[:lane.index('if route.kind == "farewell":')]
        self.assertIn("greet_routine_aware_enabled", lane)
        self.assertIn("habits.greet_aware_line", lane)
        self.assertIn("habits.today_vs_usual_line", lane)          # 旗標關的原路徑還在
        self.assertIn('_TURN["greet_claim_fallback"]', lane)
        self.assertIn("state.greet_routine_ts", lane)              # 端上桌才 stamp
        self.assertIn("greeting.is_goodnight", lane)               # 夜裡純晚安先自然收尾，不開新問題
        self.assertIn("greet_kind=gkind", lane)                     # 早安只和早安比、晚間只和晚間比
        self.assertIn("greeting.reply_is_repetitive", lane)        # 問候專屬近似重複守門
        self.assertIn('_TURN["greet_claim_fallback"] = _farewell_fallback', lane)
        # stamp 只在真的注入觀察時打（quiet/none 不打＝冷卻不被空轉刷新）；🌙 §2.27 深夜分寸也算端上桌
        self.assertIn('in ("usual", "early", "late", "smallhours")', lane)

    def test_say_consumes_fallback_once(self):
        # _say 守門：pop（消費即清、防殘留到主動 lane）且在 _habit_claim_fix 之前
        pop = self.src.index('_TURN.pop("greet_claim_fallback", "")')
        call = self.src.index("_habit_claim_fix(text, _hg, greet_fallback=_greet_fb)")
        self.assertLess(pop, call)
        # 每輪開頭也清殘留（上輪中途炸掉的保險）
        self.assertIn('_TURN.pop("greet_claim_fallback", None)', self.src)


class StatePersistTest(unittest.TestCase):
    def test_roundtrip(self):
        path = os.path.join(tempfile.mkdtemp(), "s.json")
        s = State(path)
        s.greet_routine_ts = NOW_TS
        s.save()
        self.assertEqual(State.load(path).greet_routine_ts, NOW_TS)

    def test_flag_off_state_json_unchanged(self):
        # 從沒寫過（旗標關）＝state.json 不長此鍵＝逐位元同現狀
        path = os.path.join(tempfile.mkdtemp(), "s.json")
        s = State(path)
        s.save()
        with open(path, encoding="utf-8") as f:
            self.assertNotIn("greet_routine_ts", json.load(f))
        self.assertEqual(State.load(path).greet_routine_ts, 0.0)


class FlagSyncTest(unittest.TestCase):
    def test_flag_everywhere(self):
        with open("telegram_monitor/config.py", encoding="utf-8") as f:
            src = f.read()
        self.assertIn('_bool("GREET_ROUTINE_AWARE", True)', src)   # config 預設開
        self.assertIn("greet_routine_aware_enabled", src)
        with open(".env.example", encoding="utf-8") as f:
            self.assertIn("GREET_ROUTINE_AWARE=1", f.read())
        with open("README.md", encoding="utf-8") as f:
            self.assertIn("GREET_ROUTINE_AWARE", f.read())


if __name__ == "__main__":
    unittest.main()
