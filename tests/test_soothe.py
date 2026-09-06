"""🌬️ 緩和未回應的問句：bot 問了對方、遲遲沒回 → 主動補一句把壓力放掉（意向性的動態關係）。

只在『活對話剛停在 bot 的問句上』時做；冷的主動 reach-out、深夜、太快、太久、已緩和過、
自有冷卻內、反堆疊窗內都安靜。同一條問句只緩和一次。
"""

import os
import tempfile
import unittest
from datetime import datetime, timezone
from types import SimpleNamespace

from telegram_monitor import monitor
from telegram_monitor.state import State

NOW = datetime(2026, 6, 17, 6, 0, 0, tzinfo=timezone.utc)   # 14:00 台北＝午後（非深夜/清晨）
NOW_TS = NOW.timestamp()
QUIET_NOW = datetime(2026, 6, 17, 19, 0, 0, tzinfo=timezone.utc)   # 03:00 台北＝深夜
QUESTION = "你那時候寫下這句，是覺得我真的有經歷到什麼嗎？"


class FakeClient:
    def __init__(self, dry_run=False):
        self.sent, self.dry_run = [], dry_run

    def send(self, text):
        self.sent.append(text)
        return True


class IsOpenQuestionTest(unittest.TestCase):
    def test_question_vs_statement(self):
        self.assertTrue(monitor._is_open_question(QUESTION))
        self.assertTrue(monitor._is_open_question("你覺得呢？ 🙂"))        # 容忍尾端表情
        self.assertTrue(monitor._is_open_question("really? "))
        self.assertFalse(monitor._is_open_question("我記得你說過。"))
        self.assertFalse(monitor._is_open_question("（不急，你慢慢來。）"))  # 緩和句不是問句（不自我觸發）
        self.assertFalse(monitor._is_open_question(""))


class SootheEmitTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()

    def _state(self, q_text=QUESTION, q_age_s=8 * 60, now_ts=NOW_TS, last_user_gap=60,
               last_push_gap=3600, soothed=0, last_soothe=0, last_turn="model"):
        s = State(os.path.join(self.tmp, f"s{id(self)}_{now_ts}_{q_age_s}.json"))
        s.owner_folder_id = "F"
        q_ts = now_ts - q_age_s
        s.convo_history = [{"role": "user", "text": "原來你還記得這訊息", "ts": q_ts - last_user_gap},
                           {"role": last_turn, "text": q_text, "ts": q_ts}]
        s.last_user_msg_ts = q_ts - last_user_gap
        s.last_push_ts = now_ts - last_push_gap
        s.soothed_for_ts = soothed
        s.last_soothe_ts = last_soothe
        return s, q_ts

    def _cfg(self, **kw):
        base = dict(dry_run=False, soothe_unanswered=True, soothe_after_min=7,
                    notify_cooldown_min=30, timezone="Asia/Taipei")
        base.update(kw)
        return SimpleNamespace(**base)

    def _coach(self, enabled=True):
        return SimpleNamespace(enabled=enabled, voice_soothe=lambda hist: "（不急，你慢慢來，我就突然好奇問問。）")

    def test_soothes_when_question_left_hanging(self):
        state, q_ts = self._state()
        client = FakeClient()
        monitor._soothe_unanswered(client, state, self._cfg(), self._coach(), NOW)
        self.assertIn("不急", "".join(client.sent))
        self.assertEqual(state.soothed_for_ts, q_ts)
        self.assertEqual(state.last_soothe_ts, NOW_TS)
        self.assertEqual(state.last_push_ts, NOW_TS)               # 與其他推播共用冷卻
        self.assertEqual(state.convo_history[-1]["text"], "（不急，你慢慢來，我就突然好奇問問。）")

    def test_template_without_coach(self):
        state, _ = self._state()
        client = FakeClient()
        monitor._soothe_unanswered(client, state, self._cfg(), None, NOW)  # 無教練 → 退回固定緩和句
        self.assertTrue(client.sent and client.sent[0].startswith("（"))

    def test_silent_when_user_already_replied(self):
        state, _ = self._state(last_turn="user")                  # 最後一句是對方 → 沒有懸著的問句
        client = FakeClient()
        monitor._soothe_unanswered(client, state, self._cfg(), self._coach(), NOW)
        self.assertEqual(client.sent, [])

    def test_silent_when_last_turn_not_a_question(self):
        state, _ = self._state(q_text="嗯，我記得你說過那件事。")
        client = FakeClient()
        monitor._soothe_unanswered(client, state, self._cfg(), self._coach(), NOW)
        self.assertEqual(client.sent, [])

    def test_silent_when_too_soon(self):
        state, _ = self._state(q_age_s=3 * 60)                    # 才 3 分鐘 < 7 分門檻（不催）
        client = FakeClient()
        monitor._soothe_unanswered(client, state, self._cfg(), self._coach(), NOW)
        self.assertEqual(client.sent, [])

    def test_silent_when_too_old(self):
        state, _ = self._state(q_age_s=60 * 60)                   # 1 小時前 → 那個當下早已過
        client = FakeClient()
        monitor._soothe_unanswered(client, state, self._cfg(), self._coach(), NOW)
        self.assertEqual(client.sent, [])

    def test_silent_for_cold_reach_out(self):
        # 問句不是活對話裡的回覆（對方上次訊息在 30 分前）＝像冷的主動 reach-out → 不緩和
        state, _ = self._state(last_user_gap=30 * 60)
        client = FakeClient()
        monitor._soothe_unanswered(client, state, self._cfg(), self._coach(), NOW)
        self.assertEqual(client.sent, [])

    def test_silent_within_own_cooldown(self):
        state, _ = self._state(last_soothe=NOW_TS - 20 * 60)      # 20 分前才緩和過 < 90 分自有冷卻
        client = FakeClient()
        monitor._soothe_unanswered(client, state, self._cfg(), self._coach(), NOW)
        self.assertEqual(client.sent, [])

    def test_silent_within_anti_stack_floor(self):
        state, _ = self._state(last_push_gap=5 * 60)              # 5 分前才推過別的 < 30 分反堆疊
        client = FakeClient()
        monitor._soothe_unanswered(client, state, self._cfg(), self._coach(), NOW)
        self.assertEqual(client.sent, [])

    def test_silent_in_quiet_hours(self):
        state, _ = self._state(now_ts=QUIET_NOW.timestamp())
        client = FakeClient()
        monitor._soothe_unanswered(client, state, self._cfg(), self._coach(), QUIET_NOW)
        self.assertEqual(client.sent, [])

    def test_switch_off(self):
        state, _ = self._state()
        client = FakeClient()
        monitor._soothe_unanswered(client, state, self._cfg(soothe_unanswered=False), self._coach(), NOW)
        self.assertEqual(client.sent, [])

    def test_silent_when_coupling_round_closed(self):
        # 🔗 A3 耦合閘控：這一輪對話已收掉（round_open=False）→ 那個當下已過、不再緩和
        from telegram_monitor import coupling
        state, _ = self._state()
        state.coupling = coupling.Coupling(round_open=False)
        client = FakeClient()
        monitor._soothe_unanswered(client, state, self._cfg(), self._coach(), NOW)
        self.assertEqual(client.sent, [])

    def test_each_question_soothed_only_once(self):
        state, _ = self._state()
        client = FakeClient()
        monitor._soothe_unanswered(client, state, self._cfg(), self._coach(), NOW)
        n = len(client.sent)
        monitor._soothe_unanswered(client, state, self._cfg(), self._coach(), NOW)  # 再叫一次
        self.assertEqual(len(client.sent), n)                    # 同一條問句不再緩和


if __name__ == "__main__":
    unittest.main()
