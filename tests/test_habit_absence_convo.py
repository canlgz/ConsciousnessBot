"""🌾 §1.80 對話作息也是日課（HABIT_ABSENCE_CONVO）＋措辭鬆綁——修 §1.79 對需求的兩處窄讀。

使用者更正（原話）：「我認為你要重新檢視我原來的需求」——
 ①「作息習慣」不只記寫主題：每天的早安、平常幾點會出現這些**對話層例行**也算；
 ②「他今天還沒出現」不是抑制條件、**正是**該去看看的場景（「主動與使用者閒聊看看，了解為何
   還沒看見使用者的動作」）；§1.79 原版把「不要索取回答」也禁過了頭——他要的是好奇的閒聊。
全 stub、零網路；旗標關＝§1.79 原行為。
"""

import os
import re
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

from telegram_monitor import habits, monitor, persona
from telegram_monitor.state import State

TZ = timezone(timedelta(hours=8))
BASE = datetime(2026, 7, 26, tzinfo=TZ)


def _ev(days_ago, hh, mm, k="greet_am"):
    d = (BASE - timedelta(days=days_ago)).replace(hour=hh, minute=mm)
    return {"k": k, "ts": d.timestamp()}


def _greet_hist(n=10):
    """10 天穩定 06:30–07:10 說早安（＋同刻的 msg 事件）。"""
    out = []
    for d in range(1, n + 1):
        out.append(_ev(d, 6, 30 + (d % 40)))
        out.append(_ev(d, 6, 30 + (d % 40), k="msg"))
    return out


def _now(h, m=0):
    return BASE.replace(hour=h, minute=m).timestamp()


class SamplesTest(unittest.TestCase):
    def test_greet_daily_first_excludes_today(self):
        ev = _greet_hist() + [_ev(0, 6, 40)]
        s = habits.abs_event_daily_samples(ev, ("greet_am",), TZ, _now(10))
        self.assertEqual(len(s), 10)                              # 今天那筆不進樣本

    def test_kinds_filter(self):
        s = habits.abs_event_daily_samples(_greet_hist(), ("msg", "contact"), TZ, _now(10))
        self.assertEqual(len(s), 10)
        self.assertIsNone(habits.abs_event_daily_samples(_greet_hist(), ("greet_am",), None, _now(10)))


class AppearVerdictTest(unittest.TestCase):
    def _prof(self):
        return habits.abs_routine_profile(
            habits.abs_event_daily_samples(_greet_hist(), ("greet_am",), TZ, _now(10)), _now(10), TZ,
            min_days=habits._ABS_CONVO_MIN_DAYS, max_iqr=habits._ABS_CONVO_MAX_IQR, min_p25=240)

    def test_relaxed_profile_qualifies_at_7plus_days(self):
        self.assertIsNotNone(self._prof())                        # 10 天＝過（記寫類要 14 天、這裡 7）

    def test_absent_when_not_appeared_past_due(self):
        p = self._prof()
        due = p["p75"] + habits.abs_grace_min(p["iqr"])
        self.assertEqual(habits.abs_appear_verdict(p, due + 30, False), "absent")   # 沒出現＝觸發、不是抑制

    def test_done_when_appeared(self):
        p = self._prof()
        self.assertEqual(habits.abs_appear_verdict(p, 700, True), "done")

    def test_too_early_and_window(self):
        p = self._prof()
        due = p["p75"] + habits.abs_grace_min(p["iqr"])
        self.assertEqual(habits.abs_appear_verdict(p, due - 10, False), "too_early")
        self.assertEqual(habits.abs_appear_verdict(p, due + habits._ABS_WINDOW_MIN + 10, False), "too_late")


class WordingTest(unittest.TestCase):
    def test_journal_rule_now_allows_gentle_asking(self):
        r = persona.habit_absence_rule("閱讀｜讀誦經書", "07:40", 47)
        self.assertIn("可以輕輕問一句", r)                          # ② 措辭鬆綁：他要的是閒聊了解為何
        self.assertIn("不逼問", r)
        self.assertIn("台階", r)
        self.assertNotIn("**不要**索取回答", r)                     # 原過度禁令移除

    def test_appear_rule_warm_not_checking(self):
        r = persona.appear_absence_rule("07:30", has_greet_habit=True)
        self.assertIn("早安", r)                                   # 有早安習慣＝帶進味道
        self.assertIn("惦記", r)
        self.assertIn("不催", r)
        self.assertIn("不撒嬌討抱", r)
        self.assertIn("他不回也完全沒關係", r)


class EmitTest(unittest.TestCase):
    class Cl:
        def __init__(self):
            self.sent, self.dry_run = [], False

        def send(self, t):
            self.sent.append(t)
            return True

    def setUp(self):
        monitor._TURN.clear()
        monitor._TURN["bubbles"] = None
        monitor._SENT_RECENT.clear()

    def _state(self, appeared_today=False):
        s = State(os.path.join(tempfile.mkdtemp(), "s.json"))
        s.entropy = SimpleNamespace(mood=0.0, arousal=0.0, hunger=0.3)
        s.last_user_msg_ts = _now(9) - 20 * 3600            # 昨天說過話＝今天沒出現、也不在場
        s.last_push_ts = 0
        s.coupling = None
        s.habit_events = _greet_hist() + ([_ev(0, 7, 0, k="msg")] if appeared_today else [])
        return s

    def _cfg(self, convo=True):
        return SimpleNamespace(habit_absence_enabled=True, habit_absence_convo_enabled=convo,
                               timezone="Asia/Taipei", dry_run=False, notify_cooldown_min=30)

    def _data(self):
        # 記寫資料極少＝記寫類（§1.79）不會有候選 → 走 §1.80 出現類
        return {"records": [], "meta": {}}

    def _coach(self, voice="平常這時間早就聽到你的早安了，今天還靜靜的——在忙嗎？"):
        c = SimpleNamespace(enabled=True, asked=[])

        def reply(q, *a, **k):
            c.asked.append(q)
            return voice

        c.reply = reply
        return c

    def _emit(self, s, cfg, co, hour=9):
        monitor._habit_absence_emit(self.Cl.__call__ if False else self._cl, s, cfg, co,
                                    BASE.replace(hour=hour), data=self._data())

    def test_fires_when_user_not_appeared(self):
        s, co = self._state(), self._coach()
        self._cl = self.Cl()
        monitor._habit_absence_emit(self._cl, s, self._cfg(), co, BASE.replace(hour=9), data=self._data())
        out = "".join(self._cl.sent)
        self.assertIn("靜靜的", out)                               # ② 沒出現＝觸發（§1.79 原版在此沉默）
        self.assertEqual(s.habit_absence["＠出現"]["day"], "2026-07-26")
        self.assertIn("早安", co.asked[0])                         # 早安習慣的味道進了 rule

    def test_silent_when_appeared_today(self):
        s = self._state(appeared_today=True)
        self._cl = self.Cl()
        monitor._habit_absence_emit(self._cl, s, self._cfg(), self._coach(), BASE.replace(hour=9),
                                    data=self._data())
        self.assertEqual(self._cl.sent, [])

    def test_same_day_once(self):
        s = self._state()
        s.habit_absence = {"＠出現": {"day": "2026-07-26", "ts": _now(8)}}
        self._cl = self.Cl()
        monitor._habit_absence_emit(self._cl, s, self._cfg(), self._coach(), BASE.replace(hour=9),
                                    data=self._data())
        self.assertEqual(self._cl.sent, [])

    def test_flag_off_is_179_behavior(self):
        s = self._state()
        self._cl = self.Cl()
        monitor._habit_absence_emit(self._cl, s, self._cfg(convo=False), self._coach(),
                                    BASE.replace(hour=9), data=self._data())
        self.assertEqual(self._cl.sent, [])                        # 旗標關＝§1.79：沒出現＝沉默

    def test_too_early_silent(self):
        s = self._state()
        self._cl = self.Cl()
        monitor._habit_absence_emit(self._cl, s, self._cfg(), self._coach(), BASE.replace(hour=6),
                                    data=self._data())
        self.assertEqual(self._cl.sent, [])                        # 還沒過他平常的時間窗


class ConfigTest(unittest.TestCase):
    def test_config_synced(self):
        src = open("telegram_monitor/config.py", encoding="utf-8").read()
        self.assertIn("HABIT_ABSENCE_CONVO", src)
        self.assertIn("habit_absence_convo_enabled", src)
        env = open(".env.example", encoding="utf-8").read()
        self.assertRegex(env, re.compile(r"^HABIT_ABSENCE_CONVO=1", re.M))
        self.assertIn("HABIT_ABSENCE_CONVO", open("README.md", encoding="utf-8").read())

    def test_getattr_defaults_false(self):
        self.assertFalse(getattr(SimpleNamespace(), "habit_absence_convo_enabled", False))


if __name__ == "__main__":
    unittest.main()
