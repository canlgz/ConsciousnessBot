"""🔗 互動優先於自我獨白：使用者在場（在聊或剛說過話）時，所有『報自己』的主動發話（🫀自陳/🫧伸手/🌀體驗/
🍃換檔/🪞自我修正）一律讓路；你離開後內在生活才以主動訊息流出。截圖：bot 自顧自報自己 → 你「你不理我嗎」。"""

import unittest
from types import SimpleNamespace
from unittest import mock

from telegram_monitor import monitor

NOW = 1_700_000_000.0


def _now(ts=NOW):
    return SimpleNamespace(timestamp=lambda: ts)


def _client():
    c = SimpleNamespace(sent=[], dry_run=True)
    c.send = lambda t: c.sent.append(t) or True
    return c


class UserPresentTest(unittest.TestCase):
    def test_live_round_is_present(self):
        s = SimpleNamespace(coupling=SimpleNamespace(round_open=True), last_user_msg_ts=0)
        self.assertTrue(monitor._user_present(s, NOW))

    def test_recent_message_is_present_even_if_round_closed(self):
        s = SimpleNamespace(coupling=SimpleNamespace(round_open=False), last_user_msg_ts=NOW - 60)
        self.assertTrue(monitor._user_present(s, NOW))           # 剛說過話 → 仍在場（堵住「round 剛收、你還在」）

    def test_long_silence_not_present(self):
        s = SimpleNamespace(coupling=SimpleNamespace(round_open=False), last_user_msg_ts=NOW - 9999)
        self.assertFalse(monitor._user_present(s, NOW))          # 久沒互動 → 不在場 → 內在生活可主動流出

    def test_no_coupling_no_recent(self):
        self.assertFalse(monitor._user_present(SimpleNamespace(coupling=None, last_user_msg_ts=0), NOW))


class EmitsYieldWhenPresentTest(unittest.TestCase):
    def _present_state(self, **extra):
        s = SimpleNamespace(coupling=SimpleNamespace(round_open=True), last_user_msg_ts=NOW - 10)
        for k, v in extra.items():
            setattr(s, k, v)
        return s

    def test_selfstate_emit_yields(self):
        c = _client()
        coach = SimpleNamespace(enabled=True)
        res = {"gate": 4, "reading": {"content": {"topic": "A"}}}
        monitor._selfstate_emit(c, self._present_state(), res, coach, _now())
        self.assertEqual(c.sent, [])                             # 在場 → 🫀 自陳讓路

    def test_spontaneous_emit_yields(self):
        c = _client()
        coach = SimpleNamespace(enabled=True)
        monitor._spontaneous_emit(c, self._present_state(entropy=SimpleNamespace()), SimpleNamespace(), coach, _now())
        self.assertEqual(c.sent, [])                             # 在場 → 🫧 不伸手


class MetacogYieldsWhenUserMovedOnTest(unittest.TestCase):
    def _state(self, last_user_ts):
        return SimpleNamespace(self_model={"mismatch": {"from": "hungry", "to": "stirred"}},
                               bodystate_last_ts=NOW - 30, selfstate_open_ts=0, last_metacog_ts=0,
                               last_user_msg_ts=last_user_ts, convo_history=[])

    def test_suppressed_when_user_spoke_after_report(self):
        c = _client()
        monitor._metacog_correct(c, self._state(NOW - 5), SimpleNamespace(dry_run=True), _now())
        self.assertEqual(c.sent, [])                             # 你在我報完後又說話了 → 別插更正打斷（互動優先）

    def test_fires_in_silent_gap_after_report(self):
        c = _client()
        from unittest import mock
        with mock.patch("telegram_monitor.monitor._remember"):
            monitor._metacog_correct(c, self._state(NOW - 120), SimpleNamespace(dry_run=True), _now())
        self.assertIn("其實比較像", "".join(c.sent))            # 報完後你還沒接話的安靜空檔 → 補一句更正（仍是回應你的提問）


class ProactiveOpensSelfWindowTest(unittest.TestCase):
    """🪞 bot 主動談自己/自己的意圖（🫧 reach-out）後要開『自我在場』窗——否則你回的讚美「這麼厲害」
    落工具被吐 📊（截圖根因：_spontaneous_emit 漏設 self_topic_ts）。"""

    def test_spontaneous_sets_self_topic_ts(self):
        from telegram_monitor import lifeloop
        c = _client()
        c.dry_run = False
        c.send_typing = lambda: None
        state = SimpleNamespace(
            coupling=SimpleNamespace(round_open=False), last_user_msg_ts=NOW - 99999,  # 你不在 → 可伸手
            entropy=SimpleNamespace(hunger=0.9, mood=0.0, reach_outs_this_idle=0, last_revisited_topic="研發"),
            self_state={"gate": 2, "scope": {"dominant": "研發"}}, self_topic_ts=0, last_push_ts=0,
            last_spontaneous_ts=0, convo_history=[], goals=[], focus=None, recent_self_msgs=[], save=lambda: None)
        coach = SimpleNamespace(enabled=True, voice_spontaneous=lambda seed, hist, coping="", time_rule="": "欸，我私下一直想弄懂研發那條。")
        cfg = SimpleNamespace(dry_run=False, timezone="Asia/Taipei", notify_cooldown_min=0,
                              spontaneous_cooldown_min=0, spontaneous_quiet_after_chat_min=0)
        now = SimpleNamespace(timestamp=lambda: NOW,
                              astimezone=lambda tz: SimpleNamespace(hour=15))   # 非深夜
        with mock.patch("telegram_monitor.lifeloop.spontaneous_due", return_value=True), \
             mock.patch("telegram_monitor.circadian.is_quiet_hours", return_value=False), \
             mock.patch("telegram_monitor.monitor._remember"), \
             mock.patch("telegram_monitor.monitor._set_focus"):
            monitor._spontaneous_emit(c, state, cfg, coach, now)
        self.assertTrue(c.sent)                                  # 真的伸手了
        self.assertEqual(state.self_topic_ts, NOW)               # 🪞 開了自我在場窗 → 你回的讚美會留在自我在場、不落工具


if __name__ == "__main__":
    unittest.main()
