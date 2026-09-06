"""感覺計算穩定化（F2 天花板極慢衰減＋F3 confirm 防抖狀態持久化）。"""

import os
import tempfile
import unittest

from telegram_monitor import lifeloop
from telegram_monitor.state import State


class DecayCeilingTest(unittest.TestCase):
    def test_refresh_when_ceiling_still_reached(self):
        # 確認 gate 仍達到天花板 → 重置計時、不降
        self.assertEqual(lifeloop.decay_ceiling(4, 1000.0, 4, 9999.0, 3600), (4, 9999.0))
        self.assertEqual(lifeloop.decay_ceiling(4, 1000.0, 4, 9999.0, 3600)[0], 4)

    def test_no_decay_before_window(self):
        self.assertEqual(lifeloop.decay_ceiling(4, 1000.0, 3, 1000.0 + 100, 3600), (4, 1000.0))

    def test_decay_one_level_after_long_below(self):
        peak, ts = lifeloop.decay_ceiling(4, 1000.0, 3, 1000.0 + 3600, 3600)
        self.assertEqual(peak, 3)                         # 只降一級
        self.assertEqual(ts, 1000.0 + 3600)              # 重新計時

    def test_no_peak_or_unknown_gate_is_noop(self):
        self.assertEqual(lifeloop.decay_ceiling(0, 0, 3, 5.0, 3600), (0, 0))
        self.assertEqual(lifeloop.decay_ceiling(4, 1000.0, None, 9999.0, 3600), (4, 1000.0))

    def test_unset_ts_starts_timer_no_decay(self):
        # 天花板存在但 peak_ts=0（升級前的舊狀態）→ 從現在起算、先不降（修 0 為 falsy 導致永不衰減的雷）
        peak, ts = lifeloop.decay_ceiling(4, 0, 3, 5000.0, 3600)
        self.assertEqual(peak, 4)
        self.assertEqual(ts, 5000.0)                 # 計時從現在開始

    def test_short_term_only_rises_decay_is_slow(self):
        # 短期內（< decay_s）天花板不動：抗門檻邊界抖動的「只升不降」在短期仍成立
        peak, _ = lifeloop.decay_ceiling(4, 1000.0, 1, 1000.0 + 3599, 3600)
        self.assertEqual(peak, 4)


class ConfirmStatePersistTest(unittest.TestCase):
    def test_confirm_debounce_and_ceiling_ts_persist(self):
        p = os.path.join(tempfile.mkdtemp(), "s.json")
        s = State(p)
        s.gate_confirmed, s.gate_raw_last, s.gate_raw_run = 3, 4, 2
        s.confirmed_res = {"gate": 3, "sensitivity": 1.5}
        s.notified_self_gate, s.notified_self_gate_ts = 4, 123.0
        s.save()
        s2 = State.load(p)
        self.assertEqual(s2.gate_confirmed, 3)           # 重啟後互動讀數不落空/退化（與主動同源）
        self.assertEqual(s2.gate_raw_last, 4)
        self.assertEqual(s2.gate_raw_run, 2)             # 防抖計數不歸零
        self.assertEqual(s2.confirmed_res["gate"], 3)
        self.assertEqual(s2.notified_self_gate, 4)
        self.assertEqual(s2.notified_self_gate_ts, 123.0)  # 天花板衰減計時跨重生

    def test_defaults_clean_on_first_run(self):
        s = State(os.path.join(tempfile.mkdtemp(), "nope.json"))
        self.assertIsNone(s.gate_confirmed)
        self.assertEqual(s.gate_raw_run, 0)
        self.assertEqual(s.notified_self_gate_ts, 0)


if __name__ == "__main__":
    unittest.main()
