"""🧭 §1.35 散開重複的問候也要被看見：全域 300s 窄窗數不到「隔幾分鐘一次的早安」→ 用寬窗補數同類問候連發、
導出漸進覺察等級（2→L1 點出/3→L2 好奇/≥4→L3 情緒），餵現成 user_repeat_fatigue_hint。

截圖（07:08–07:18 連五次「早安」）：bot 每次吐一模一樣的問候、零覺察。根因：窄窗 count 停在 3、達不到升級門檻 4，
且問候路徑本身無反重複。修：寬窗補數。防誤傷：單次問候 count=1 不觸發；「早安」接「晚安」sig 不近似、串斷。
"""

import datetime
import unittest
from types import SimpleNamespace

from telegram_monitor import dialogue_intent as DI, persona

_BASE = datetime.datetime(2026, 7, 13, 7, 0, 0).timestamp()


def _cfg():
    return SimpleNamespace(dialogue_intent_enabled=True, intent_log_max=20,
                           intent_sim_threshold=0.85, greeting_repeat_window_sec=1200)


def _wide_count(kinds_at_min):
    st = SimpleNamespace(user_model={})
    now = _BASE
    for text, m in kinds_at_min:
        now = _BASE + m * 60
        DI.observe(st, text, "greeting", now, _cfg())
    return DI.repetition_run(st.user_model.get("intent_log"), now, 1200, 0.85).get("count", 0)


def _esc_from_count(wc):
    return 0 if wc < 2 else (1 if wc == 2 else (2 if wc == 3 else 3))


class GreetingRepeatAwareTest(unittest.TestCase):
    def test_screenshot_five_morning_escalates_progressively(self):
        # 截圖時間戳：07:08,09,14,15,18 —— 寬窗數到 5、導出 L3
        counts = []
        st = SimpleNamespace(user_model={})
        for m in (8, 9, 14, 15, 18):
            now = _BASE + m * 60
            DI.observe(st, "早安", "greeting", now, _cfg())
            counts.append(DI.repetition_run(st.user_model.get("intent_log"), now, 1200, 0.85).get("count"))
        self.assertEqual(counts, [1, 2, 3, 4, 5])
        self.assertEqual([_esc_from_count(c) for c in counts], [0, 1, 2, 3, 3])

    def test_single_greeting_no_trigger(self):
        self.assertEqual(_wide_count([("早安", 0)]), 1)          # 只說一次＝不觸發（防誤傷正常使用者）
        self.assertEqual(_esc_from_count(_wide_count([("早安", 0)])), 0)

    def test_morning_then_night_not_merged(self):
        self.assertEqual(_wide_count([("早安", 0), ("晚安", 1)]), 1)   # sig 不近似→連發串斷、不誤併

    def test_L3_gentle_when_not_testing(self):
        # 散開問候 akind='greeting'（非 testing）→ L3 一律 gentle、絕不呵斥/罵人
        h = persona.user_repeat_fatigue_hint("greeting", 3, ceiling="stern")
        for scold in ("夠了", "別再洗", "把話說重", "翻臉"):
            self.assertNotIn(scold, h)
        self.assertIn("善意", h)

    def test_L1_L2_perceive_and_inquire(self):
        h1 = persona.user_repeat_fatigue_hint("greeting", 1)
        h2 = persona.user_repeat_fatigue_hint("greeting", 2)
        self.assertTrue(h1 and h2)
        self.assertNotEqual(h1, h2)                             # 漸進：L1≠L2

    def test_flag_field_exists(self):
        from telegram_monitor.config import Config
        c = Config.load(".env")
        self.assertIsInstance(c.greeting_repeat_aware_enabled, bool)
        self.assertTrue(c.greeting_repeat_aware_enabled)        # 真部署預設開


if __name__ == "__main__":
    unittest.main()
