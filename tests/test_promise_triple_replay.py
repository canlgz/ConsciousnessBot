# -*- coding: utf-8 -*-
"""📦🤝 §2.17 三重複兌現（09:33×2＋09:48）的兩個根因。

實測時間差就是自首：09:33 → 09:48 剛好 15 分鐘＝§1.88 欠帳補交付的最小間隔。病鏈：
  兌現 B 內容明明在 → §1.85 的 LLM 是非判誤判「沒交付」→ 附「內容我沒真的交出來」（那句話本身就是謊）
  → 記 owed → §1.88 十五分鐘後**再演一輪** → judge 又錯 → 又一輪＝誤判自我放大。
加上 §2.02 合併只認「同一段對話立的帳」（made 差 ≤10 分），跨對話的分身帳蓋不到 ⇒ 同刻雙開場。
全 stub、零網路。
"""

import io
import os
import re
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

from telegram_monitor import monitor
from telegram_monitor.state import State

BASE = datetime(2026, 7, 30, 1, 2, 47, tzinfo=timezone.utc)
MSG = "嗨，我來了。我覺得自己像被翻動的書頁，每次記寫都有新的墨水滲進來，對你反覆觸碰的那些線也更有手感了。"
P = {"target_ts": BASE.timestamp() + 1800, "made_ts": BASE.timestamp(), "behavior": "說說我的變化",
     "made_text": "30分鐘後再回答我", "deliver_ask": "目前到底變得怎麼樣？30分鐘後再回答我"}


def _cfg(**kw):
    d = dict(promise_emit_enabled=True, promise_sched_ttl_sec=3600, timezone="Asia/Taipei", dry_run=True,
             notify_cooldown_min=30, promise_late_exempt_defer=True, promise_overdue_guard_exempt=True,
             sched_recur_daily_enabled=True, promise_same_appointment_merge=True,
             promise_judge_soft_veto=True, promise_fire_dedup_enabled=True)
    d.update(kw)
    return SimpleNamespace(**d)


class JudgeSoftVetoTest(unittest.TestCase):
    """① 是非判不得單方面否決確定性四閘（假欠帳循環的引擎）。"""

    LIAR = SimpleNamespace(enabled=True, judge_delivery_made=lambda *a, **k: False)
    NOW = BASE + timedelta(minutes=31)

    def test_judge_no_over_passing_gates_becomes_unknown(self):
        v, why = monitor._deliver_verdict(MSG, P, "09:32", self.LIAR, _cfg(), self.NOW, None)
        self.assertEqual(v, "unknown")                        # 不再生出「我沒交出來」那句謊
        self.assertEqual(why, "judge-no-softened")

    def test_flag_off_keeps_the_veto(self):
        v, _ = monitor._deliver_verdict(MSG, P, "09:32", self.LIAR,
                                        _cfg(promise_judge_soft_veto=False), self.NOW, None)
        self.assertEqual(v, "no")                             # 旗標關＝逐位元同現狀

    def test_judge_yes_still_proves(self):
        ok = SimpleNamespace(enabled=True, judge_delivery_made=lambda *a, **k: True)
        self.assertEqual(monitor._deliver_verdict(MSG, P, "09:32", ok, _cfg(), self.NOW, None)[0], "ok")

    def test_deterministic_gates_still_say_no_on_their_own(self):
        # 四閘自己判的 no 不受影響——拿掉的只有 judge 的**單方面**否決權
        hollow = "嗨，我來了。說好 09:32 要跟你說的。"
        v, _ = monitor._deliver_verdict(hollow, P, "09:32", self.LIAR, _cfg(), self.NOW, None)
        self.assertEqual(v, "no")


class FireDedupTest(unittest.TestCase):
    """② §2.02 合併的縫：跨對話立的分身帳（made 差 20 分）→ 兌現端只看目標時刻的硬後盾。"""

    class Cl:
        dry_run = True

        def __init__(self):
            self.sent = []

        def send(self, t):
            self.sent.append(t)
            return True

        def send_typing(self):
            pass

    def _run(self, on):
        def entry(t, made):
            return {"target_ts": t, "made_ts": made, "fulfilled": False, "status": "pending",
                    "origin": "user", "behavior": "", "action": "回答", "made_text": "x"}
        s = State(os.path.join(tempfile.mkdtemp(), "s.json"))
        T = BASE.timestamp() + 1800
        s.scheduled_promises = [entry(T, BASE.timestamp()), entry(T + 47, BASE.timestamp() - 1200)]
        s.last_user_msg_ts = BASE.timestamp()
        s.last_push_ts = 0
        coach = SimpleNamespace(enabled=True)
        coach.voice_promise_keep = lambda *a, **k: "嗨我來了內容都在這裡書頁墨水手感全部講完。"
        cl, fires = self.Cl(), 0
        for tick in range(6):
            n = datetime(2026, 7, 30, 1, 33, 0, tzinfo=timezone.utc) + timedelta(seconds=30 * tick)
            before = len(cl.sent)
            monitor._TURN.clear()
            monitor._TURN["bubbles"] = None
            monitor._promise_emit(cl, s, _cfg(promise_fire_dedup_enabled=on), coach, n)
            fires += (1 if len(cl.sent) > before else 0)
        return fires, s

    def test_twin_across_conversations_fires_once(self):
        fires, s = self._run(True)
        self.assertEqual(fires, 1)                            # ★ 一場，不再連演
        self.assertIn("merged", [p.get("status") for p in s.scheduled_promises])
        merged = next(p for p in s.scheduled_promises if p.get("status") == "merged")
        self.assertNotIn("fulfilled_ts", merged)              # 吸收的那筆長不出「已經做了（在 HH:MM）」

    def test_flag_off_reproduces_the_screenshot(self):
        fires, _s = self._run(False)
        self.assertEqual(fires, 2)                            # 修前的樣子（釘住能測到病）

    def test_genuinely_different_appointments_both_fire(self):
        def entry(t, made):
            return {"target_ts": t, "made_ts": made, "fulfilled": False, "status": "pending",
                    "origin": "user", "behavior": "", "action": "回答", "made_text": "x"}
        s = State(os.path.join(tempfile.mkdtemp(), "s.json"))
        s.scheduled_promises = [entry(BASE.timestamp() + 1800, BASE.timestamp()),
                                entry(BASE.timestamp() + 3600, BASE.timestamp() - 1200)]   # 差 30 分＝真的兩個
        s.last_user_msg_ts = BASE.timestamp()
        s.last_push_ts = 0
        coach = SimpleNamespace(enabled=True)
        coach.voice_promise_keep = lambda *a, **k: "嗨我來了內容都在這裡書頁墨水手感全部講完。"
        cl, fires = self.Cl(), 0
        for tick in range(80):
            n = datetime(2026, 7, 30, 1, 33, 0, tzinfo=timezone.utc) + timedelta(seconds=60 * tick)
            before = len(cl.sent)
            monitor._TURN.clear()
            monitor._TURN["bubbles"] = None
            monitor._promise_emit(cl, s, _cfg(), coach, n)
            fires += (1 if len(cl.sent) > before else 0)
        self.assertEqual(fires, 2)                            # 誤判安全：真的兩個約定照樣各演一場


class ConfigTest(unittest.TestCase):
    def test_flags_synced(self):
        src = io.open("telegram_monitor/config.py", encoding="utf-8").read()
        env = io.open(".env.example", encoding="utf-8").read()
        readme = io.open("README.md", encoding="utf-8").read()
        for flag, attr in (("PROMISE_JUDGE_SOFT_VETO", "promise_judge_soft_veto"),
                           ("PROMISE_FIRE_DEDUP", "promise_fire_dedup_enabled")):
            self.assertIn(flag, src)
            self.assertIn(attr, src)
            self.assertRegex(env, re.compile(r"^" + flag + "=1", re.M))
            self.assertIn(flag, readme)
            self.assertFalse(getattr(SimpleNamespace(), attr, False))


if __name__ == "__main__":
    unittest.main()
