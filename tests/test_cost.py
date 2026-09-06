"""CostMeter 測試（注入 now，純函式、確定性）。"""

import unittest

from telegram_monitor.cost import CostMeter


def _meter(threshold=10.0, window_min=10, cooldown_min=30):
    # 單價：input 1.0、output 2.0（USD/1M）；匯率 10 → 方便算
    return CostMeter(in_per_m=1.0, out_per_m=2.0, usd_twd=10.0,
                     window_min=window_min, threshold_twd=threshold, cooldown_min=cooldown_min)


class CostMeterTest(unittest.TestCase):
    def test_record_cost_math(self):
        m = _meter()
        # 1,000,000 input + 1,000,000 output → (1.0 + 2.0) USD = 3 USD → ×10 = NT$30
        cost = m.record(1_000_000, 1_000_000, now=0)
        self.assertAlmostEqual(cost, 3.0, places=6)
        self.assertAlmostEqual(m.window_twd(now=0), 30.0, places=4)

    def test_alert_fires_then_cooldown(self):
        m = _meter(threshold=10.0, window_min=10, cooldown_min=30)
        m.record(0, 5_000_000, now=0)        # 5 USD output → NT$100，超過門檻
        a = m.check_alert(now=0)
        self.assertIsNotNone(a)
        self.assertGreaterEqual(a["window_twd"], 10.0)
        # 冷卻內不再觸發
        self.assertIsNone(m.check_alert(now=60))
        # 過了冷卻、且視窗內又有新花費 → 再觸發
        m.record(0, 5_000_000, now=31 * 60)
        self.assertIsNotNone(m.check_alert(now=31 * 60))

    def test_window_prunes_old(self):
        m = _meter(threshold=10.0, window_min=10)
        m.record(0, 5_000_000, now=0)         # NT$100 在 t=0
        self.assertAlmostEqual(m.window_twd(now=0), 100.0, places=2)
        # 11 分鐘後，舊事件已滑出視窗 → 0
        self.assertAlmostEqual(m.window_twd(now=11 * 60), 0.0, places=6)
        self.assertIsNone(m.check_alert(now=11 * 60))

    def test_disabled_when_threshold_zero(self):
        m = _meter(threshold=0)
        m.record(0, 9_999_999, now=0)
        self.assertFalse(m.enabled)
        self.assertIsNone(m.check_alert(now=0))


if __name__ == "__main__":
    unittest.main()
