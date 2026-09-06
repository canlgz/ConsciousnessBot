"""🎴🔇 §1.33 貼圖維護總開關：SEND_STICKERS=0 時，_system 注入「維護中、絕不宣稱送/挑貼圖」規則，
蓋掉 SOCRATIC_SYSTEM 的「你做得到送貼圖」——止血「謊稱挑了貼圖沒送」連環截圖。旗標非 0＝逐位元同現狀。"""

import os
import unittest
from types import SimpleNamespace
from unittest.mock import patch

from telegram_monitor import coach as C, persona


def _coach():
    cfg = SimpleNamespace(gemini_api_key="k", gemini_model="m", gemini_price_in_per_m=0.3,
                          gemini_price_out_per_m=2.5, usd_twd_rate=32, cost_window_min=10,
                          cost_alert_twd=10, cost_alert_cooldown_min=30)
    return C.Coach(cfg)


class StickerMaintenanceTest(unittest.TestCase):
    def test_off_injects_maintenance_rule(self):
        with patch.dict(os.environ, {"SEND_STICKERS": "0"}):
            sys = _coach()._system()
        self.assertIn("維護中", sys)
        self.assertIn("絕不", sys)
        self.assertIn(persona.STICKER_MAINTENANCE_HINT, sys)

    def test_on_bit_identical(self):
        with patch.dict(os.environ, {"SEND_STICKERS": "1"}):
            sys = _coach()._system()
        self.assertNotIn("貼圖功能維護中", sys)
        self.assertIn("送真的貼圖也是你真做得到", sys)   # 原能力宣稱保留＝同現狀

    def test_default_absent(self):
        env = dict(os.environ)
        env.pop("SEND_STICKERS", None)
        with patch.dict(os.environ, env, clear=True):
            sys = _coach()._system()
        self.assertNotIn("貼圖功能維護中", sys)          # 預設（未設）＝同現狀

    def test_maintenance_rule_forbids_claims(self):
        h = persona.STICKER_MAINTENANCE_HINT
        for kw in ("送不出", "絕不", "老實說", "不要", "emoji", "附件"):
            self.assertIn(kw, h)


if __name__ == "__main__":
    unittest.main()
