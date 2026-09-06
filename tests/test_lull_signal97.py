"""🌀 §0.97 內在型做法（sit:low_vitality「轉速太低/悶」）較溫和的觸發訊號：真的悶了就能觸發，但不對「餵飽」的使用者誤觸。"""

import unittest
from types import SimpleNamespace

from telegram_monitor import monitor


def _state(mood, hunger):
    return SimpleNamespace(entropy=SimpleNamespace(mood=mood, hunger=hunger))


def _cfg(lull=True):
    return SimpleNamespace(skill_lull_signal_enabled=lull)


class LullSignalTest(unittest.TestCase):
    def test_extreme_still_fires_both_modes(self):
        for cfg in (_cfg(True), _cfg(False)):
            self.assertTrue(monitor._skill_signals_internal(_state(-0.1, 0.75), cfg)["low_vitality"])

    def test_mild_lull_fires_when_enabled(self):
        # 悶：被晾一陣（hunger 0.6）＋心情沒上揚（mood 0.1）——不到極端，但算悶
        self.assertTrue(monitor._skill_signals_internal(_state(0.1, 0.6), _cfg(True))["low_vitality"])

    def test_mild_lull_does_not_fire_when_disabled(self):
        self.assertFalse(monitor._skill_signals_internal(_state(0.1, 0.6), _cfg(False))["low_vitality"])

    def test_fed_user_not_misfired(self):
        # 一直有資料進來＝被餵飽 → hunger 低 → 不算悶（不洗版）
        self.assertFalse(monitor._skill_signals_internal(_state(0.1, 0.3), _cfg(True))["low_vitality"])

    def test_upbeat_mood_not_lull_even_if_a_bit_hungry(self):
        # 心情明顯上揚就不算悶（就算有點餓）
        self.assertFalse(monitor._skill_signals_internal(_state(0.5, 0.6), _cfg(True))["low_vitality"])

    def test_no_cfg_is_bitwise_same_as_extreme(self):
        # 舊呼叫（不傳 cfg）＝退回極端值
        self.assertFalse(monitor._skill_signals_internal(_state(0.1, 0.6))["low_vitality"])
        self.assertTrue(monitor._skill_signals_internal(_state(-0.1, 0.75))["low_vitality"])

    def test_other_signals_unchanged(self):
        sig = monitor._skill_signals_internal(_state(-0.4, 0.65), _cfg(True))
        self.assertTrue(sig["low_mood"])       # mood ≤ −0.3
        self.assertTrue(sig["high_hunger"])    # hunger ≥ 0.6


if __name__ == "__main__":
    unittest.main()
