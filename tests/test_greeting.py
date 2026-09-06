"""🕘 問候的絕對時間感：早安/午安/晚安 對照真實時段；對不上→說出 bot 自己的疑惑/感受（不照單全收）。"""

import os
import sys
import unittest
from datetime import datetime

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from telegram_monitor import greeting, persona


def _at(hour, minute=15):
    return datetime(2026, 6, 24, hour, minute)


class DetectTest(unittest.TestCase):
    def test_basic_greetings(self):
        self.assertEqual(greeting.detect("早安"), "morning")
        self.assertEqual(greeting.detect("午安"), "noon")
        self.assertEqual(greeting.detect("晚安"), "night")

    def test_variants_and_punctuation(self):
        self.assertEqual(greeting.detect("早安！"), "morning")
        self.assertEqual(greeting.detect("晚安～"), "night")
        self.assertEqual(greeting.detect("早上好"), "morning")
        self.assertEqual(greeting.detect("下午好"), "noon")
        self.assertEqual(greeting.detect("早"), "morning")          # 整句就是「早」才算

    def test_not_a_greeting(self):
        self.assertIsNone(greeting.detect("早餐吃什麼"))            # 含「早」但不是問候
        self.assertIsNone(greeting.detect("早點睡比較好"))          # 同上
        self.assertIsNone(greeting.detect(""))
        self.assertIsNone(greeting.detect("我今天午餐吃了很多東西然後想睡"))   # 長句、非問候


class ClosingAndVarietyTest(unittest.TestCase):
    def test_pure_goodnight_is_closing_but_evening_hello_is_not(self):
        for text in ("晚安", "晚安囉", "晚安呀", "晚安安", "晚安😴", "晚安～😴",
                     "good night", "晚安，我先睡了"):
            self.assertTrue(greeting.is_goodnight(text), text)
        for text in ("晚上好", "晚安，你還醒著嗎？"):
            self.assertFalse(greeting.is_goodnight(text), text)

    def test_recent_greeting_replies_are_paired_by_role(self):
        hist = [{"role": "user", "text": "早安"},
                {"role": "model", "text": "你今天出現得比平常早了一些，是發生了什麼嗎？"},
                {"role": "user", "text": "別的話"},
                {"role": "model", "text": "這不是問候回覆"},
                {"role": "user", "text": "晚安"},
                {"role": "model", "text": "晚安，好好休息。"}]
        self.assertEqual(greeting.recent_reply_texts(hist),
                         ["你今天出現得比平常早了一些，是發生了什麼嗎？"])
        self.assertEqual(greeting.recent_reply_texts(hist, limit=0), [])

    def test_plain_greeting_does_not_swallow_attached_content(self):
        self.assertTrue(greeting.is_plain_greeting("早安🥰"))
        self.assertFalse(greeting.is_plain_greeting("早安，我今天很累"))

    def test_evening_hello_fallback_is_not_goodnight(self):
        out = greeting.response_text("night", _at(21), recent=[])
        self.assertIn("晚上好", out)
        self.assertNotIn("好好休息", out)

    def test_fresh_fallback_avoids_recent_exact_line(self):
        recent = ["早安，我在。", "早安。看到你來了。"]
        self.assertEqual(greeting.fresh_text("morning", seq=24, recent=recent), "早安，今天也慢慢來。")

    def test_screenshot_early_late_formula_is_detected(self):
        old = ["你今天出現得比平常晚好多，是發生了什麼嗎？"]
        new = "你今天出現得比平常早了一些，是發生了什麼嗎？"
        self.assertTrue(greeting.reply_is_repetitive(new, old))
        self.assertFalse(greeting.reply_is_repetitive("早安，我在。", old))

    def test_fallback_and_prompt_do_not_force_question(self):
        self.assertNotIn("？", greeting.fresh_text("morning", 1))
        hint = persona.greeting_variety_hint(["你今天出現得比平常晚好多，是發生了什麼嗎？"])
        self.assertIn("不必每次", hint)
        self.assertIn("不是指令", hint)
        self.assertIn("固定骨架", hint)


class TimeMatchTest(unittest.TestCase):
    def test_match(self):
        self.assertEqual(greeting.time_match("morning", "早上"), "match")
        self.assertEqual(greeting.time_match("noon", "中午"), "match")
        self.assertEqual(greeting.time_match("night", "晚上"), "match")
        self.assertEqual(greeting.time_match("night", "深夜"), "match")

    def test_mismatch(self):
        self.assertEqual(greeting.time_match("night", "早上"), "mismatch")   # 截圖：早上說晚安
        self.assertEqual(greeting.time_match("noon", "早上"), "mismatch")    # 截圖：早上說午安
        self.assertEqual(greeting.time_match("morning", "晚上"), "mismatch")


class FactsTest(unittest.TestCase):
    def test_match_is_warm_not_alarmed(self):
        f = greeting.facts("morning", _at(9))                       # 09:15＝早上 → 對得上
        self.assertIn("對得上", f)
        self.assertIn("09:15", f)

    def test_mismatch_asks_for_own_confusion_not_echo(self):
        f = greeting.facts("night", _at(9))                        # 早上說晚安 → 對不上
        self.assertIn("對不上", f)
        self.assertIn("09:15", f)
        self.assertIn("疑惑", f)                                    # 要說出自己的疑惑/感受
        self.assertIn("照單全收", f)                                # 明確禁止照單全收


class TextTemplateTest(unittest.TestCase):
    def test_match_templates(self):
        self.assertIn("早安", greeting.text("morning", _at(9)))     # 早上說早安
        self.assertIn("晚安", greeting.text("night", _at(22)))      # 晚上說晚安

    def test_mismatch_shows_real_time_and_feeling(self):
        t = greeting.text("night", _at(9))                         # 早上說晚安
        self.assertIn("早上", t)                                    # 點出真實時段
        self.assertTrue("愣" in t or "困惑" in t)                   # 帶 bot 自己的感受
        self.assertNotEqual(t, "晚安，好好休息喔。")                # 絕不照單全收

    def test_noon_mismatch_in_morning(self):
        t = greeting.text("noon", _at(9))
        self.assertIn("困惑", t)


if __name__ == "__main__":
    unittest.main()
