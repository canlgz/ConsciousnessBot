# -*- coding: utf-8 -*-
"""🤖 §2.21 幽靈約定殺手＋數字在場判定放寬。

實測截圖（22:23–22:24）：bot 演出一場使用者從沒約過的守約（「這是說好 22:23 要跟你說的」）、
被質疑還堅持「我之前答應過你」、最後查帳才認錯。機制（temporal 實測釘死）：前一天 22:23 的澄清句
「現在是 22:23，我們約 20 分鐘後，也就是 22:43…我會…」——「22:23」剛過去被滾成**明天同時刻**，
22:43 被 §1.18 (c) 鄰近去重（帳上已有）⇒ 只剩幽靈錨入帳。全 stub、零網路。
"""

import os
import re
import tempfile
import unittest
from datetime import datetime
from types import SimpleNamespace
from zoneinfo import ZoneInfo

from telegram_monitor import monitor, temporal
from telegram_monitor.state import State

TZ = ZoneInfo("Asia/Taipei")
NOW = datetime(2026, 7, 30, 22, 23, 40, tzinfo=TZ)
CLAR = "我的意思是，現在是 22:23，我們約 20 分鐘後，也就是 22:43 的時候，我會來跟你說這段時間內我的情緒座標有沒有變動。"


def _run(text, flag):
    s = State(os.path.join(tempfile.mkdtemp(), "s.json"))
    s.scheduled_promises = [{"target_ts": NOW.timestamp() + 1180, "made_ts": NOW.timestamp() - 60,
                             "fulfilled": False, "status": "pending", "origin": "user", "behavior": ""}]
    cfg = SimpleNamespace(bot_self_promise_enabled=True, self_promise_dedup_enabled=True,
                          promise_same_appointment_merge=True, self_promise_no_rolled=flag,
                          self_promise_trace_enabled=True, promise_mood_ground_enabled=False,
                          timezone="Asia/Taipei", dry_run=True)
    coach = SimpleNamespace(enabled=True, judge_self_promise=lambda t: (True, "回報座標"))
    monitor._TURN.clear()
    monitor._TURN["self_promise_ctx"] = (s, cfg, coach, NOW, TZ, NOW.timestamp(), "fact_or_chat")
    monitor._TURN["self_promise_trace"] = True
    monitor._maybe_self_promise_capture(text)
    return s


class RolledAnchorTest(unittest.TestCase):
    def test_temporal_really_rolls_the_current_clock(self):
        # 釘住機制：澄清句裡的「22:23」（剛過）被滾成明天同時刻
        eps = temporal.all_clock_epochs(CLAR, NOW, TZ)
        self.assertTrue(any(abs(e - 86400 - NOW.timestamp()) <= 900 for e in eps))

    def test_ghost_is_dropped(self):
        s = _run(CLAR, True)
        self.assertEqual([p.get("origin") for p in s.scheduled_promises], ["user"])   # 只剩本來那筆

    def test_flag_off_reproduces_the_ghost(self):
        s = _run(CLAR, False)
        ghost = [p for p in s.scheduled_promises if p.get("origin") == "self"]
        self.assertEqual(len(ghost), 1)
        d = datetime.fromtimestamp(ghost[0]["target_ts"], TZ)
        self.assertEqual(d.strftime("%H:%M"), "22:23")                                # 明天 22:23＝截圖那場

    def test_genuine_tomorrow_promise_still_books(self):
        s = _run("明天早上11點我會過來叫你。", True)
        self.assertEqual(len([p for p in s.scheduled_promises if p.get("origin") == "self"]), 1)


class UnsignedNumbersCountTest(unittest.TestCase):
    def test_keep_with_unsigned_delta_gets_no_duplicate_line(self):
        s = State(os.path.join(tempfile.mkdtemp(), "s.json"))
        p = {"target_ts": NOW.timestamp() - 60, "made_ts": NOW.timestamp() - 1860, "behavior": "跟他回報",
             "made_text": "20分鐘後告訴我座標變動", "fulfilled": False, "status": "pending",
             "mood_baseline": monitor._mood_snapshot(s, NOW.timestamp() - 1860)}
        cfg = SimpleNamespace(promise_mood_ground_enabled=True, mood_coord_deliver_enabled=True,
                              promise_mood_numbers_enabled=True, promise_delivery_proof_enabled=False,
                              promise_deliver_content_enabled=False, promise_teaser_hollow_enabled=False,
                              timezone="Asia/Taipei", dry_run=True, sched_recur_daily_enabled=True)
        coach = SimpleNamespace(enabled=True)
        coach.voice_promise_keep = lambda *a, **k: "從上次到現在，V 降了 0.46、A 降了 0.35，整體鬆了一些。"
        msg = monitor._promise_keep_body(s, cfg, coach, p, NOW, TZ, False)
        self.assertEqual(msg.count("0.46"), 1)                # ★ 同一個 Δ 不再用括號講第二遍

    def test_no_numbers_still_appended(self):
        s = State(os.path.join(tempfile.mkdtemp(), "s.json"))
        p = {"target_ts": NOW.timestamp() - 60, "made_ts": NOW.timestamp() - 1860, "behavior": "跟他回報",
             "made_text": "20分鐘後告訴我座標變動", "fulfilled": False, "status": "pending",
             "mood_baseline": monitor._mood_snapshot(s, NOW.timestamp() - 1860)}
        cfg = SimpleNamespace(promise_mood_ground_enabled=True, mood_coord_deliver_enabled=True,
                              promise_mood_numbers_enabled=True, promise_delivery_proof_enabled=False,
                              promise_deliver_content_enabled=False, promise_teaser_hollow_enabled=False,
                              timezone="Asia/Taipei", dry_run=True, sched_recur_daily_enabled=True)
        coach = SimpleNamespace(enabled=True)
        coach.voice_promise_keep = lambda *a, **k: "感覺鬆了一些、沉了一點。"
        msg = monitor._promise_keep_body(s, cfg, coach, p, NOW, TZ, False)
        self.assertRegex(msg, re.compile(r"\d\.\d\d"))        # §2.20 的保證仍在

    def test_mood_backstop_requires_complete_current_pair(self):
        monitor._TURN.clear()
        monitor._TURN.update({"bubbles": None, "mood_data_line": "（我此刻的座標是 V +0.10、A +0.05。）"})
        cl = SimpleNamespace(sent=[], dry_run=False)
        cl.send = lambda t: cl.sent.append(t) or True
        monitor._say(cl, "我的 V 大概降了 0.46 左右。")
        # 單軸 delta 不是「此刻 V/A」；仍要補上完整、等於凍結快照的 current pair。
        self.assertTrue(any("座標是 V +0.10、A +0.05" in x for x in cl.sent))


class ConfigTest(unittest.TestCase):
    def test_flag_synced(self):
        import io
        src = io.open("telegram_monitor/config.py", encoding="utf-8").read()
        self.assertIn("SELF_PROMISE_NO_ROLLED", src)
        self.assertIn("self_promise_no_rolled", src)
        self.assertRegex(io.open(".env.example", encoding="utf-8").read(),
                         re.compile(r"^SELF_PROMISE_NO_ROLLED=1", re.M))
        self.assertIn("SELF_PROMISE_NO_ROLLED", io.open("README.md", encoding="utf-8").read())
        self.assertFalse(getattr(SimpleNamespace(), "self_promise_no_rolled", False))


if __name__ == "__main__":
    unittest.main()
