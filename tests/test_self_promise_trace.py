# -*- coding: utf-8 -*-
"""🤖🔁 §2.03 兩個實測到的洞（截圖 13:57–13:58）。

① **答應了卻沒排程**：bot 說「五十分鐘後，我會好好想想再告訴你」，14:21 打 `/promises`
   **還沒做的 0 筆** ⇒ 那句到 14:47 不會發生。實測兩個閘：使用者那句 §1.12 `judge_timed_request`
   判 False（連拼句也是）、bot 自己那句 §1.18 `judge_self_promise` 判 True('好好想想再告訴你')
   ⇒ 照理該入帳卻沒有；而**查不下去**是因為這條鏈每個出口都是裸 `return`——
   「答應了卻沒排程」在外面跟「根本沒答應」長得一模一樣。⇒ 每個出口留痕，`/promises` 講得出卡在哪一關。

② **重播守門的罐頭句**：使用者把一個意思拆成連著兩則送，第二則的回覆逐字重複第一則 ⇒ 整則剝空 ⇒
   換上「這段我剛剛才說過一次——你想聽哪部分，我換個說法講？」＝把 bot 自己的重複說成他要求重講、
   又把選擇權丟回去（§1.87 已定案要禁的形狀）。⇒ 同一波改用短承接。全 stub、零網路。
"""

import io
import os
import re
import tempfile
import unittest
from datetime import datetime, timezone
from types import SimpleNamespace
from zoneinfo import ZoneInfo

from telegram_monitor import monitor
from telegram_monitor.state import State

TZ = ZoneInfo("Asia/Taipei")
NOW = datetime(2026, 7, 28, 13, 57, 10, tzinfo=TZ)
BOT_LINE = "嗯，能幫你實現願望，這感覺真好。五十分鐘後，我會好好想想再告訴你。"


def _state():
    s = State(os.path.join(tempfile.mkdtemp(), "s.json"))
    s.scheduled_promises = []
    return s


def _cfg(**kw):
    d = dict(bot_self_promise_enabled=True, self_promise_dedup_enabled=True, dry_run=True,
             timezone="Asia/Taipei", promise_mood_ground_enabled=False,
             promise_same_appointment_merge=True)
    d.update(kw)
    return SimpleNamespace(**d)


def _ctx(state, coach, route="fact_or_chat"):
    return (state, _cfg(), coach, NOW, TZ, NOW.timestamp(), route)


def _coach(verdict=(True, "好好想想再告訴你"), raise_exc=None):
    def _j(_t):
        if raise_exc:
            raise raise_exc
        return verdict
    return SimpleNamespace(enabled=True, judge_self_promise=_j)


class TraceTest(unittest.TestCase):
    """每個出口都要留痕——這條鏈以前每個出口都是裸 return，所以外面查不到是哪一關丟的。"""

    def setUp(self):
        monitor._TURN.clear()
        monitor._TURN["self_promise_trace"] = True

    def _run(self, text=BOT_LINE, coach=None, route="fact_or_chat", state=None, skip=False):
        s = state or _state()
        monitor._TURN["self_promise_ctx"] = _ctx(s, coach or _coach(), route)
        if skip:
            monitor._TURN["self_promise_skip"] = True
        monitor._maybe_self_promise_capture(text)
        return s, list(getattr(s, "self_promise_log", None) or [])

    def test_booked_leaves_a_trace_too(self):
        s, log = self._run()
        self.assertEqual(len(s.scheduled_promises), 1)              # 真的進帳了
        self.assertTrue(log[-1]["booked_ts"])                       # 而且看得出來
        self.assertIn("五十分鐘後", log[-1]["text"])

    def test_judge_says_no(self):
        _s, log = self._run(coach=_coach(verdict=(False, "")))
        self.assertIsNone(log[-1]["booked_ts"])
        self.assertIn("複誦", log[-1]["why"])

    def test_judge_error(self):
        from telegram_monitor import gemini
        _s, log = self._run(coach=_coach(raise_exc=gemini.GeminiError("boom")))
        self.assertIn("判定閘出錯", log[-1]["why"])

    def test_route_excluded(self):
        _s, log = self._run(route="scheduled_promise")
        self.assertIn("scheduled_promise", log[-1]["why"])

    def test_ack_skip(self):
        _s, log = self._run(skip=True)
        self.assertIn("已經另外入帳", log[-1]["why"])

    def test_no_future_anchor(self):
        _s, log = self._run(text="嗯，我會努力的。")
        self.assertIn("沒有可解的未來時刻", log[-1]["why"])

    def test_already_on_the_books(self):
        s = _state()
        s.scheduled_promises = [{"target_ts": NOW.timestamp() + 3000, "made_ts": NOW.timestamp(),
                                 "fulfilled": False, "status": "pending"}]
        _s, log = self._run(state=s)
        self.assertIn("已經在帳上", log[-1]["why"])

    def test_structure_miss_leaves_nothing(self):
        _s, log = self._run(text="今天天氣真好。")                  # 連「我會」都沒有＝零成本跳出、不必留痕
        self.assertEqual(log, [])

    def test_trace_never_breaks_sending(self):
        monitor._sp_trace(object(), BOT_LINE, "壞掉的 state 也不能炸")   # 留痕永遠不得打斷送訊
        monitor._sp_trace(None, BOT_LINE, "沒有 state 也不能炸")

    def test_flag_off_records_nothing(self):
        monitor._TURN["self_promise_trace"] = False
        s, log = self._run()
        self.assertEqual(log, [])
        self.assertEqual(len(s.scheduled_promises), 1)              # 但入帳行為完全不變


class AuditShowsMissTest(unittest.TestCase):
    def test_promises_lists_the_unbooked_sentence_and_why(self):
        s = _state()
        s.self_promise_log = [{"ts": NOW.timestamp(), "text": BOT_LINE, "why": "判定閘說這句是複誦/確認，不是新的約定",
                               "booked_ts": None}]
        out = monitor._promises_audit(s, _cfg(), NOW.timestamp() + 60, TZ)
        self.assertIn("沒進帳本", out)
        self.assertIn("五十分鐘後", out)
        self.assertIn("複誦", out)

    def test_booked_ones_are_not_nagged_about(self):
        s = _state()
        s.self_promise_log = [{"ts": NOW.timestamp(), "text": BOT_LINE, "why": "", "booked_ts": NOW.timestamp() + 3000}]
        self.assertNotIn("沒進帳本", monitor._promises_audit(s, _cfg(), NOW.timestamp() + 60, TZ))


class SameWaveAckTest(unittest.TestCase):
    """🔁 §2.03 同一波的第二則被剝空 → 短承接，而不是把選擇權丟回去的罐頭。"""

    class Cl:
        def __init__(self):
            self.sent, self.dry_run = [], False

        def send(self, t):
            self.sent.append(t)
            return True

    def _out(self, same_wave):
        monitor._TURN.clear()
        monitor._TURN.update({"bubbles": None, "replay_guard": True, "replay_same_wave": same_wave})
        dup = "嗯，能幫你實現願望，這感覺真好，五十分鐘後我會好好想想再告訴你"
        monitor._replay_note(dup)
        cl = self.Cl()
        monitor._say(cl, dup)
        return "".join(cl.sent)

    def test_same_wave_gets_a_short_ack(self):
        out = self._out(True)
        self.assertIn(out, monitor._SAME_WAVE_ACKS)
        self.assertNotIn("你想聽哪部分", out)                        # ★ 不再把選擇權丟回去
        self.assertLessEqual(len(out), 8)                            # 一個「嗯」的份量

    def test_not_same_wave_keeps_old_line(self):
        self.assertEqual(self._out(False), monitor._REPLAY_FALLBACK)

    def test_acks_rotate(self):
        seen = {monitor._same_wave_ack() for _ in range(12)}
        self.assertGreater(len(seen), 1)                             # 不會變成同一句口頭禪
        for a in monitor._SAME_WAVE_ACKS:
            for claim in ("記", "會", "答應"):
                self.assertNotIn(claim, a)                           # 純承接、不帶任何宣稱

    def test_wave_flag_computed_before_last_user_ts_is_overwritten(self):
        src = io.open("telegram_monitor/monitor.py", encoding="utf-8").read()
        i = src.index('_TURN["replay_same_wave"]')
        j = src.index("state.last_user_msg_ts = now_utc.timestamp()", i - 2000)
        self.assertLess(i, j)                                        # 順序錯了就永遠算不出同一波


class ConfigTest(unittest.TestCase):
    def test_flags_synced(self):
        src = io.open("telegram_monitor/config.py", encoding="utf-8").read()
        env = io.open(".env.example", encoding="utf-8").read()
        readme = io.open("README.md", encoding="utf-8").read()
        for flag, attr in (("SELF_PROMISE_TRACE", "self_promise_trace_enabled"),
                           ("REPLAY_WAVE_ACK", "replay_wave_ack_enabled")):
            self.assertIn(flag, src)
            self.assertIn(attr, src)
            self.assertRegex(env, re.compile(r"^" + flag + "=1", re.M))
            self.assertIn(flag, readme)
            self.assertFalse(getattr(SimpleNamespace(), attr, False))


if __name__ == "__main__":
    unittest.main()
