"""環境適應工作流（sense→detect→adapt）：感知周遭活絡度/晝夜、遲滯偵測換檔、適性調轉速/姿態；
並驗證接進生命迴圈的接線：心跳轉速吃環境倍率（手動 /pulse 仍最優先）、換檔含蓄自陳。"""

import os
import tempfile
import unittest
from datetime import datetime, timezone
from types import SimpleNamespace

from telegram_monitor import environ, lifeloop, monitor
from telegram_monitor.state import State


def _entropy(laps):
    e = lifeloop.EntropyState()
    e.laps_since_fresh = laps
    return e

DAY = datetime(2026, 6, 21, 13, 0)    # 下午（非深夜）
NIGHT = datetime(2026, 6, 21, 3, 0)   # 深夜（quiet hours）


class ReadEnvironmentTest(unittest.TestCase):
    def test_fresh_data_is_active(self):
        r = environ.read_environment(laps_since_fresh=0, secs_since_talk=None, now_local=DAY)
        self.assertEqual(r.activity, 1.0)
        self.assertFalse(r.quiet)

    def test_recent_talk_is_active_even_if_data_cold(self):
        r = environ.read_environment(laps_since_fresh=99, secs_since_talk=0, now_local=DAY)
        self.assertEqual(r.activity, 1.0)              # 對話面拉滿（取兩者大者）

    def test_both_cold_is_quiet_world(self):
        r = environ.read_environment(laps_since_fresh=99, secs_since_talk=None, now_local=DAY)
        self.assertEqual(r.activity, 0.0)

    def test_partial_activity_blends_max(self):
        # laps 15→data_act .5；talk 15 分→talk_act .5 → 取大者 .5
        r = environ.read_environment(15, 15 * 60, DAY)
        self.assertAlmostEqual(r.activity, 0.5, places=3)

    def test_circadian_wired(self):
        self.assertTrue(environ.read_environment(0, 0, NIGHT).quiet)
        self.assertFalse(environ.read_environment(0, 0, DAY).quiet)


class AdaptTest(unittest.TestCase):
    def _r(self, activity, quiet=False):
        return environ.EnvReading(activity, "下午", quiet)

    def test_active_quickens_to_min(self):
        self.assertEqual(environ.adapt(self._r(1.0)).pace_mult, environ._PACE_MIN)

    def test_cold_slows_to_max(self):
        self.assertEqual(environ.adapt(self._r(0.0)).pace_mult, environ._PACE_MAX)

    def test_mid_is_between(self):
        # .5 活絡 → 0.5 + 0.5*(3.0-0.5) = 1.75
        self.assertAlmostEqual(environ.adapt(self._r(0.5)).pace_mult, 1.75, places=3)

    def test_night_slows_same_activity_further(self):
        day = environ.adapt(self._r(0.4, quiet=False)).pace_mult
        night = environ.adapt(self._r(0.4, quiet=True)).pace_mult
        self.assertGreater(night, day)                 # 夜裡同樣冷清→更慢
        self.assertLessEqual(night, environ._PACE_MAX)  # 仍夾在上限內

    def test_busy_night_not_slowed(self):
        # 活絡（≥_ACT_HI）即使深夜也不硬放慢（有人在）
        self.assertEqual(environ.adapt(self._r(0.6, quiet=True)).pace_mult,
                         environ.adapt(self._r(0.6, quiet=False)).pace_mult)

    def test_pace_clamped(self):
        for a in (0.0, 0.3, 0.5, 0.8, 1.0):
            p = environ.adapt(self._r(a, quiet=True)).pace_mult
            self.assertGreaterEqual(p, environ._PACE_MIN)
            self.assertLessEqual(p, environ._PACE_MAX)


class StanceHintTest(unittest.TestCase):
    def test_extremes_hint_middle_silent(self):
        self.assertTrue(environ.stance_hint(environ.EnvReading(0.1, "下午", False)))   # 很冷清→染
        self.assertTrue(environ.stance_hint(environ.EnvReading(0.9, "下午", False)))   # 很熱絡→染
        self.assertEqual(environ.stance_hint(environ.EnvReading(0.5, "下午", False)), "")

    def test_quiet_hours_suppress_lively_stance(self):
        # 深夜即使很熱絡也不喊「醒一點、俐落些」（與晝夜柔語氣矛盾）→ 讓晝夜主導
        self.assertEqual(environ.stance_hint(environ.EnvReading(0.9, "深夜", True)), "")
        self.assertTrue(environ.stance_hint(environ.EnvReading(0.9, "下午", False)))   # 白天照常熱絡染
        self.assertTrue(environ.stance_hint(environ.EnvReading(0.1, "深夜", True)))    # 很冷清的染語氣不受影響


class DetectShiftTest(unittest.TestCase):
    def _r(self, activity, quiet=False):
        return environ.EnvReading(activity, "下午", quiet)

    def test_first_reading_is_baseline_no_shift(self):
        es = environ.EnvState()
        self.assertIsNone(es.update(self._r(0.9)))     # 第一拍只記基線
        self.assertEqual(es.band, "high")

    def test_quieting_and_livening(self):
        es = environ.EnvState()
        es.update(self._r(0.9))                         # 基線 high
        self.assertEqual(es.update(self._r(0.1)), "quieting")
        self.assertEqual(es.update(self._r(0.9)), "livening")

    def test_hysteresis_holds_within_band(self):
        es = environ.EnvState()
        es.update(self._r(0.9))                         # high
        self.assertIsNone(es.update(self._r(0.4)))      # 落在 [.25,.5) 帶內 → 維持 high、不換檔
        self.assertEqual(es.band, "high")
        self.assertEqual(es.update(self._r(0.2)), "quieting")  # 跌破整個帶才換

    def test_nightfall_daybreak_when_activity_steady(self):
        es = environ.EnvState()
        es.update(self._r(0.1, quiet=False))            # 基線 low/白天
        self.assertEqual(es.update(self._r(0.1, quiet=True)), "nightfall")
        self.assertEqual(es.update(self._r(0.1, quiet=False)), "daybreak")

    def test_activity_shift_takes_priority_over_circadian(self):
        es = environ.EnvState()
        es.update(self._r(0.1, quiet=True))             # 基線 low/深夜
        # 同拍：活絡度 low→high（livening）＋ 深夜→白天（daybreak）→ 只報活絡換檔
        self.assertEqual(es.update(self._r(0.9, quiet=False)), "livening")


class ReportLineTest(unittest.TestCase):
    def test_shift_lines_nonempty_and_vary(self):
        self.assertTrue(environ.report_line("quieting", 0))
        self.assertTrue(environ.report_line("livening", 0))
        # 不同 seed 取到不同句（模板輪替）
        lines = {environ.report_line("quieting", t) for t in range(3)}
        self.assertGreater(len(lines), 1)

    def test_non_activity_shift_is_silent(self):
        self.assertEqual(environ.report_line("nightfall", 0), "")
        self.assertEqual(environ.report_line(None, 0), "")


class WiringTest(unittest.TestCase):
    """接進生命迴圈：轉速吃環境倍率、手動覆蓋最優先、ADAPT_ENABLED=0 還原；換檔含蓄自陳＋抑制。"""

    def _state(self):
        s = State(os.path.join(tempfile.mkdtemp(), "s.json"))
        return s

    def _client(self):
        c = SimpleNamespace(sent=[], dry_run=False)
        c.send = lambda t: c.sent.append(t) or True
        return c

    def _cfg(self, **kw):
        base = dict(lifeloop_wait_secs=3.0, adapt_enabled=True, adapt_announce=True,
                    adapt_announce_cooldown_min=120, notify_cooldown_min=30, dry_run=False)
        base.update(kw)
        return SimpleNamespace(**base)

    # ── 心跳轉速：環境倍率 × 設定檔，手動 /pulse 最優先 ──
    def test_loop_wait_applies_env_pace_mult(self):
        s = self._state()
        s.env_pace_mult = 2.0
        self.assertEqual(monitor._loop_wait_secs(self._cfg(), s), 6.0)   # 3.0 × 2.0

    def test_manual_pulse_override_beats_env(self):
        s = self._state()
        s.env_pace_mult, s.pulse_override = 3.0, 1.0
        self.assertEqual(monitor._loop_wait_secs(self._cfg(), s), 1.0)   # 手動說了算、環境讓位

    def test_loop_wait_floor(self):
        s = self._state()
        s.env_pace_mult = 0.01
        self.assertEqual(monitor._loop_wait_secs(self._cfg(lifeloop_wait_secs=3.0), s), 0.5)  # 下限

    # ── 適應環：算出轉速/姿態存進 state ──
    def test_adapt_sets_pace_and_stance(self):
        s, c = self._state(), self._client()
        s.entropy = _entropy(99)        # 資料全冷
        s.last_user_msg_ts = 0                                  # 從沒聊過 → 全冷
        cycle = {"now": datetime(2026, 6, 21, 13, tzinfo=timezone.utc)}
        monitor._environ_adapt(c, s, self._cfg(), None, cycle, timezone.utc)
        self.assertEqual(s.env_pace_mult, environ._PACE_MAX)    # 冷清→最慢
        self.assertTrue(s.env_stance)                           # 很冷清→姿態染色非空

    def test_fresh_birth_starts_calm_not_max_pace(self):
        # 剛出生（entropy 還沒建、沒聊過）→ 視為全冷＝最慢（啟動即平靜），而非誤判周遭最熱鬧、一啟動就衝最快
        s, c = self._state(), self._client()       # State() 預設 entropy=None
        monitor._environ_adapt(c, s, self._cfg(), None,
                               {"now": datetime(2026, 6, 21, 13, tzinfo=timezone.utc)}, timezone.utc)
        self.assertEqual(s.env_pace_mult, environ._PACE_MAX)

    def test_disabled_resets_to_neutral(self):
        s, c = self._state(), self._client()
        s.env_pace_mult, s.env_stance = 2.0, "x"
        monitor._environ_adapt(c, s, self._cfg(adapt_enabled=False), None,
                               {"now": datetime(2026, 6, 21, 13, tzinfo=timezone.utc)}, timezone.utc)
        self.assertEqual(s.env_pace_mult, 1.0)
        self.assertEqual(s.env_stance, "")
        self.assertEqual(c.sent, [])

    # ── 換檔自陳：冷清換檔時含蓄出聲；冷卻/連發/深夜則噤聲 ──
    def _quieting_call(self, s, c, cfg, now):
        s.env = environ.EnvState()
        s.env.band = "high"                                    # 預置基線＝熱絡 → 這拍跌成冷清＝quieting
        s.entropy = _entropy(99)
        s.last_user_msg_ts = 0
        monitor._environ_adapt(c, s, cfg, None, {"now": now}, timezone.utc)

    def test_announces_on_quieting(self):
        s, c = self._state(), self._client()
        now = datetime(2026, 6, 21, 13, tzinfo=timezone.utc)    # 白天、非深夜
        self._quieting_call(s, c, self._cfg(), now)
        self.assertTrue(c.sent)                                 # 出聲了（長句經 _say 可能分成多串）
        self.assertIn("🍃", c.sent[0])                          # 前綴在第一串
        self.assertEqual("".join(c.sent), "🍃 " + environ.report_line("quieting", now.timestamp()))
        self.assertEqual(s.last_adapt_announce_ts, now.timestamp())
        self.assertEqual(s.last_push_ts, now.timestamp())      # 與其他推播共用冷卻

    def test_silent_when_recently_pushed(self):
        s, c = self._state(), self._client()
        now = datetime(2026, 6, 21, 13, tzinfo=timezone.utc)
        s.last_push_ts = now.timestamp()                       # 剛推過別的 → 共用冷卻擋住
        self._quieting_call(s, c, self._cfg(), now)
        self.assertEqual(c.sent, [])
        self.assertEqual(s.env_pace_mult, environ._PACE_MAX)   # 但轉速照樣默默調

    def test_silent_in_quiet_hours(self):
        s, c = self._state(), self._client()
        night = datetime(2026, 6, 21, 3, tzinfo=timezone.utc)  # 深夜 → 不打擾
        self._quieting_call(s, c, self._cfg(), night)
        self.assertEqual(c.sent, [])

    def test_announce_off(self):
        s, c = self._state(), self._client()
        now = datetime(2026, 6, 21, 13, tzinfo=timezone.utc)
        self._quieting_call(s, c, self._cfg(adapt_announce=False), now)
        self.assertEqual(c.sent, [])


if __name__ == "__main__":
    unittest.main()
