# -*- coding: utf-8 -*-
"""🤝 §2.18 履約後的靜默關注：我交出去的東西有沒有落地。

使用者定調：「履行約定一段時間後，若仍沒有收到使用者的任何回應，也許可以暗示或很簡單的問問使用者，
保持 bot 隨時關注使用者的任何回應動向，讓 bot 更有意識感。」全 stub、零網路。
"""

import io
import os
import re
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

from telegram_monitor import monitor, persona
from telegram_monitor.state import State

NOW = datetime(2026, 7, 30, 4, 0, tzinfo=timezone.utc)
KEEP_TS = NOW.timestamp() - 35 * 60          # 35 分鐘前履約


def _state(**kw):
    s = State(os.path.join(tempfile.mkdtemp(), "s.json"))
    s.keep_followup = {"ts": KEEP_TS, "beh": "說說我的變化", "asked": False}
    s.last_user_msg_ts = KEEP_TS - 3600      # 履約之後他一直沒說話
    s.last_push_ts = 0
    for k, v in kw.items():
        setattr(s, k, v)
    return s


def _cfg(**kw):
    d = dict(promise_keep_followup_enabled=True, notify_cooldown_min=30,
             timezone="Asia/Taipei", quiet_start=1, quiet_end=6, dry_run=True)
    d.update(kw)
    return SimpleNamespace(**d)


class Cl:
    dry_run = True

    def __init__(self):
        self.sent = []

    def send(self, t):
        self.sent.append(t)
        return True

    def send_typing(self):
        pass


COACH = SimpleNamespace(enabled=False)


class KeepFollowupTest(unittest.TestCase):
    def test_asks_once_when_user_stayed_silent(self):
        s, cl = _state(), Cl()
        monitor._keep_followup_emit(cl, s, _cfg(), COACH, NOW)
        self.assertEqual(len(cl.sent), 1)
        self.assertTrue(cl.sent[0].startswith("🤝 "))
        self.assertTrue(s.keep_followup["asked"])
        monitor._keep_followup_emit(cl, s, _cfg(), COACH, NOW + timedelta(minutes=40))
        self.assertEqual(len(cl.sent), 1)                       # ★ 只問一次

    def test_followup_is_the_one_same_promise_exception(self):
        # 🤐 全域規則會壓住「沒有回覆就另開主動話題」；這則則是剛履行的同一件
        # 約定之一次確認，因此仍能問一次。
        s, cl = _state(last_push_ts=KEEP_TS), Cl()
        monitor._keep_followup_emit(cl, s, _cfg(unanswered_proactive_guard_enabled=True), COACH, NOW)
        self.assertEqual(len(cl.sent), 1)

    def test_user_replied_means_landed_never_ask(self):
        s, cl = _state(last_user_msg_ts=KEEP_TS + 60), Cl()     # 履約後他回過話
        monitor._keep_followup_emit(cl, s, _cfg(), COACH, NOW)
        self.assertEqual(cl.sent, [])
        self.assertIsNone(s.keep_followup)                      # 錨清掉＝落地了

    def test_too_early_waits(self):
        s, cl = _state(), Cl()
        s.keep_followup["ts"] = NOW.timestamp() - 10 * 60       # 才 10 分鐘
        monitor._keep_followup_emit(cl, s, _cfg(), COACH, NOW)
        self.assertEqual(cl.sent, [])
        self.assertFalse(s.keep_followup["asked"])              # 之後還會問

    def test_expired_never_backfills(self):
        s, cl = _state(), Cl()
        s.keep_followup["ts"] = NOW.timestamp() - 4 * 3600      # 4 小時前＝當下過去了
        monitor._keep_followup_emit(cl, s, _cfg(), COACH, NOW)
        self.assertEqual(cl.sent, [])
        self.assertIsNone(s.keep_followup)                      # 不事後翻舊帳

    def test_user_present_defers(self):
        s, cl = _state(), Cl()
        s.last_user_msg_ts = NOW.timestamp() - 60               # 在場
        monitor._keep_followup_emit(cl, s, _cfg(), COACH, NOW)
        self.assertEqual(cl.sent, [])                           # 他回話了→下輪判定會把錨清掉

    def test_flag_off_is_inert(self):
        s, cl = _state(), Cl()
        monitor._keep_followup_emit(cl, s, _cfg(promise_keep_followup_enabled=False), COACH, NOW)
        self.assertEqual(cl.sent, [])

    def test_anchor_recorded_on_delivery_only(self):
        # settle_delivered（_dlv 非 False）才記錨；owed 不記（那走 §1.88）
        s = _state(keep_followup=None)
        p = {"target_ts": KEEP_TS, "behavior": "說說我的變化", "recur": ""}
        monitor._promise_settle_delivered(p, _cfg(), NOW.timestamp(), True, state=s)
        self.assertIsNotNone(s.keep_followup)
        s2 = _state(keep_followup=None)
        p2 = {"target_ts": KEEP_TS, "behavior": "x", "recur": "",
              "_dv": {"delivered": False, "ts": NOW.timestamp(), "sample": "夠長的樣本字串拿去比對用"}}
        monitor._promise_settle_delivered(p2, _cfg(), NOW.timestamp(), True, state=s2)
        self.assertIsNone(s2.keep_followup)                     # owed 不記錨

    def test_state_roundtrip(self):
        p = os.path.join(tempfile.mkdtemp(), "s.json")
        s = State(p)
        s.keep_followup = {"ts": 1.0, "beh": "x", "asked": False}
        s.save()
        self.assertEqual(State.load(p).keep_followup["beh"], "x")

    def test_prompt_discipline_no_examples(self):
        r = persona.keep_followup_rule("說說我的變化")
        self.assertIn("最多一個問句", r)
        self.assertIn("不准**把剛才的內容再講一遍" if "不准**把" in r else "再講一遍", r)
        self.assertIn("台階", r)
        self.assertIn("說說我的變化", r)

    def test_wired_into_feel_chain(self):
        src = io.open("telegram_monitor/monitor.py", encoding="utf-8").read()
        i = src.index('_keep_followup_emit(client, state, cfg, coach, cycle["now"])')
        self.assertGreater(i, src.index('_promise_emit(client, state, cfg, coach, cycle["now"])'))


class ConfigTest(unittest.TestCase):
    def test_flag_synced(self):
        src = io.open("telegram_monitor/config.py", encoding="utf-8").read()
        self.assertIn("PROMISE_KEEP_FOLLOWUP", src)
        self.assertIn("promise_keep_followup_enabled", src)
        self.assertRegex(io.open(".env.example", encoding="utf-8").read(),
                         re.compile(r"^PROMISE_KEEP_FOLLOWUP=1", re.M))
        self.assertIn("PROMISE_KEEP_FOLLOWUP", io.open("README.md", encoding="utf-8").read())
        self.assertFalse(getattr(SimpleNamespace(), "promise_keep_followup_enabled", False))


if __name__ == "__main__":
    unittest.main()
