# -*- coding: utf-8 -*-
"""🤝 §2.02 同一個約定只來一次（PROMISE_SAME_APPOINTMENT）。

截圖根因（12:20–12:21，使用者：「bot 似乎重複回應了同一次約定，造成回應囉唆冗長，
一點都不像有意識的對話回應行為」）：**同一個約定進帳兩次** ⇒ 到點連來兩則「我來了」＋兩輪儀式句。

為什麼兩處既有去重都沒擋住（實測，非猜）：它們都是**固定 epsilon**（捕捉端 60 秒、§1.89 端 90 秒），
但「30 分鐘後」這種**相對時距**，使用者說完到 bot 回完之間隔了多久，兩筆帳的目標時刻就差多久——
11:49:20 說「30分鐘之後」→ 12:19:20；11:50:35 回「30 分鐘後，我會再過來找你」→ 12:20:35，**差 75 秒**。
固定 epsilon 對相對時距永遠會漏；容差本來就該隨回覆延遲伸縮。

修法兩層：入帳端容差改成「底線＋這筆帳立到現在多久」；**兌現端結構性後盾**——一趟兌現把同一個約定的
其他帳一起結掉（帳本裡已經躺著的重複筆也救得到）。全 stub、零網路。
"""

import io
import os
import re
import tempfile
import unittest
from datetime import datetime, timedelta
from types import SimpleNamespace
from zoneinfo import ZoneInfo

from telegram_monitor import monitor
from telegram_monitor.state import State

TZ = ZoneInfo("Asia/Taipei")


def at(h, m, s=0):
    return datetime(2026, 7, 28, h, m, s, tzinfo=TZ)


U_TS = at(11, 49, 20).timestamp()          # 使用者：「30分鐘之後，在證明一次給我看」
B_TS = at(11, 50, 35).timestamp()          # bot 回：「30 分鐘後，我會再過來找你…」＝晚 75 秒


def _p(target, made, origin="user", **kw):
    d = {"target_ts": target, "made_ts": made, "fulfilled": False, "status": "pending",
         "origin": origin, "behavior": "", "action": "證明一次", "made_text": "30 分鐘後…"}
    d.update(kw)
    return d


def _ledger():
    return [_p(U_TS + 1800, U_TS, "user"), _p(B_TS + 1800, B_TS, "self")]


class Cl:
    dry_run = False

    def __init__(self):
        self.sent = []

    def send(self, t):
        self.sent.append(t)
        return True

    def send_typing(self):
        pass


def _cfg(merge=True, **kw):
    d = dict(promise_emit_enabled=True, promise_sched_ttl_sec=3600, timezone="Asia/Taipei",
             dry_run=True, notify_cooldown_min=30, promise_late_exempt_defer=True,
             promise_overdue_guard_exempt=True, sched_recur_daily_enabled=True,
             promise_same_appointment_merge=merge)
    d.update(kw)
    return SimpleNamespace(**d)


def _state(proms=None):
    s = State(os.path.join(tempfile.mkdtemp(), "s.json"))
    s.scheduled_promises = _ledger() if proms is None else proms
    s.last_user_msg_ts = at(11, 57, 0).timestamp()
    s.last_push_ts = 0
    return s


def _coach():
    c = SimpleNamespace(enabled=True)
    c.voice_promise_keep = lambda *a, **k: "嗨，我來了。說好的時間到了。這就是我要證明給你看的事。"
    return c


class SameAppointmentTest(unittest.TestCase):
    def test_relative_offset_drift_is_the_same_appointment(self):
        # ★ 根因本身：差 75 秒＝回覆延遲，不是兩個約定
        a, b = _ledger()
        self.assertAlmostEqual(abs(a["target_ts"] - b["target_ts"]), 75, places=0)
        self.assertTrue(monitor._same_appointment(a, b))
        self.assertTrue(monitor._same_appointment(b, a))          # 對稱

    def test_identical_clock_promises(self):
        t = at(19, 0).timestamp()
        self.assertTrue(monitor._same_appointment(_p(t, U_TS), _p(t, U_TS + 40)))

    def test_genuinely_different_appointments_stay_separate(self):
        self.assertFalse(monitor._same_appointment(_p(U_TS + 1800, U_TS), _p(U_TS + 3600, U_TS + 30)))

    def test_made_in_a_different_conversation_is_not_the_same(self):
        # 立帳時間差超過 10 分＝不同段對話；即使時刻很近也算兩個約定
        self.assertFalse(monitor._same_appointment(_p(U_TS + 1800, U_TS),
                                                   _p(U_TS + 1800 + 30, U_TS + 700)))


class EmitTest(unittest.TestCase):
    """實跑 _promise_emit 連續數拍——這是使用者真正看到的那條路。"""

    def _run(self, merge):
        s, seen = _state(), []
        for tick in range(8):
            now = at(12, 19, 30) + timedelta(seconds=30 * tick)
            cl = Cl()
            monitor._TURN.clear()
            monitor._TURN["bubbles"] = None
            monitor._promise_emit(cl, s, _cfg(merge), _coach(), now)
            seen.extend(cl.sent)
        return s, seen

    def test_flag_off_reproduces_the_screenshot(self):
        _s, seen = self._run(False)
        self.assertEqual(sum(1 for t in seen if "我來了" in t), 2)     # ★ 同一個約定兩則「我來了」

    def test_merge_makes_it_speak_once(self):
        s, seen = self._run(True)
        self.assertEqual(sum(1 for t in seen if "我來了" in t), 1)
        self.assertEqual([p.get("status") for p in s.scheduled_promises], ["fulfilled", "merged"])

    def test_merged_entry_never_claims_a_finish_time(self):
        s, _seen = self._run(True)
        merged = [p for p in s.scheduled_promises if p.get("status") == "merged"][0]
        self.assertTrue(merged["fulfilled"])
        self.assertNotIn("fulfilled_ts", merged)                    # 帳本長不出「已經做了（在 HH:MM）」
        self.assertIn("merged_into_ts", merged)                     # 但留痕，看得出是被吸收的

    def test_merged_entry_is_not_picked_up_as_owed(self):
        # 📦 §1.88 的欠帳主動補只認 status=='owed' → merged 不會再被推一次（否則又是第二則）
        s, _seen = self._run(True)
        self.assertNotIn("owed", [p.get("status") for p in s.scheduled_promises])

    def test_two_real_appointments_still_both_fire(self):
        # 誤判安全：真的兩個約定（差 30 分）不得被合併掉
        proms = [_p(at(12, 19).timestamp(), U_TS, "user"), _p(at(12, 49).timestamp(), U_TS + 20, "user")]
        s, seen = _state(proms), []
        for tick in range(70):
            now = at(12, 18, 30) + timedelta(seconds=30 * tick)
            cl = Cl()
            monitor._TURN.clear()
            monitor._TURN["bubbles"] = None
            monitor._promise_emit(cl, s, _cfg(True), _coach(), now)
            seen.extend(cl.sent)
        self.assertEqual(sum(1 for t in seen if "我來了" in t), 2)


class BookingDedupTest(unittest.TestCase):
    """入帳端：bot 自諾撞上剛入帳的使用者約定（§1.89 的固定 90 秒對 75 秒漂移剛好還擋得住，
    但真實延遲更久就漏——這裡用 100 秒漂移釘住『容差要隨延遲伸縮』）。"""

    def _book(self, merge, drift):
        s = _state([_p(U_TS + 1800, U_TS, "user")])
        cfg = _cfg(merge, self_promise_dedup_enabled=True, promise_mood_ground_enabled=False)
        monitor._book_self_promise(s, cfg, [U_TS + 1800 + drift], "30 分鐘後我會過來找你",
                                   U_TS + drift, "再證明一次")
        return s.scheduled_promises

    def test_drift_beyond_fixed_epsilon_is_still_deduped(self):
        self.assertEqual(len(self._book(True, 100)), 1)             # 旗標開＝容差隨延遲伸縮
        self.assertEqual(len(self._book(False, 100)), 2)            # 旗標關＝固定 90 秒＝漏

    def test_far_apart_still_books(self):
        self.assertEqual(len(self._book(True, 1200)), 2)            # 差 20 分＝真的是另一個約定


class PromisesAuditTest(unittest.TestCase):
    """🤝 §2.02 /promises：帳本是本 repo 出事最多的子系統，卻一直沒有辦法看到它。"""

    def _audit(self, s, merge=True):
        return monitor._promises_audit(s, _cfg(merge), at(12, 10).timestamp(), TZ)

    def test_empty(self):
        s = _state([])
        self.assertIn("一筆都沒有", self._audit(s))

    def test_lists_entries_with_who_and_when(self):
        out = self._audit(_state())
        self.assertIn("12:19:20", out)
        self.assertIn("12:20:35", out)
        self.assertIn("你要求的", out)
        self.assertIn("我自己說的", out)                            # 分得出哪筆是 bot 自諾
        self.assertIn("還沒到／等著做", out)

    def test_flags_the_duplicate_pair(self):
        out = self._audit(_state())
        self.assertIn("同一個約定記了兩筆", out)                     # ★ 直接點名，不用他自己比對時刻
        self.assertNotIn("同一個約定記了兩筆", self._audit(_state([_p(U_TS + 1800, U_TS)])))

    def test_says_when_the_merge_flag_is_off(self):
        self.assertIn("PROMISE_SAME_APPOINTMENT=0", self._audit(_state(), merge=False))

    def test_command_wired(self):
        src = io.open("telegram_monitor/monitor.py", encoding="utf-8").read()
        self.assertIn('low.startswith(("/promises", "/約定", "/帳本"))', src)


class ConfigTest(unittest.TestCase):
    def test_flag_synced(self):
        src = io.open("telegram_monitor/config.py", encoding="utf-8").read()
        self.assertIn("PROMISE_SAME_APPOINTMENT", src)
        self.assertIn("promise_same_appointment_merge", src)
        env = io.open(".env.example", encoding="utf-8").read()
        self.assertRegex(env, re.compile(r"^PROMISE_SAME_APPOINTMENT=1", re.M))
        self.assertIn("PROMISE_SAME_APPOINTMENT", io.open("README.md", encoding="utf-8").read())

    def test_getattr_defaults_false(self):
        self.assertFalse(getattr(SimpleNamespace(), "promise_same_appointment_merge", False))

    def test_merge_is_inert_without_state(self):
        self.assertEqual(monitor._promise_merge_siblings(None, _cfg(True), _p(U_TS + 1800, U_TS), U_TS), 0)


if __name__ == "__main__":
    unittest.main()
