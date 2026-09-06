"""⏱🧠 通盤常理審查（專屬結構化 pass）：只據接地事實判使用者這句的前提有無明顯違反常理。
通用、不靠列舉；失敗/OK→None；違和→帶一句人話 why。"""

import unittest
from types import SimpleNamespace
from unittest import mock

from telegram_monitor import coach as cm, persona


def _coach():
    return cm.Coach(SimpleNamespace(gemini_api_key="k", gemini_model="m"))


class PremiseCheckTest(unittest.TestCase):
    def test_violation_parsed(self):
        with mock.patch("telegram_monitor.coach.gemini.generate", return_value="違和：現在是晚上、不是早餐時間"):
            r = _coach().premise_check("你吃過早餐了嗎", "2026/06/25 週四 19:13（晚上）", [])
        self.assertEqual(r, {"off": True, "why": "現在是晚上、不是早餐時間"})

    def test_ok_returns_none(self):
        with mock.patch("telegram_monitor.coach.gemini.generate", return_value="OK"):
            self.assertIsNone(_coach().premise_check("今天天氣如何", "x", []))

    def test_error_returns_none(self):
        with mock.patch("telegram_monitor.coach.gemini.generate", side_effect=cm.gemini.GeminiError("boom")):
            self.assertIsNone(_coach().premise_check("x", "y", []))

    def test_violation_without_why_is_none(self):
        with mock.patch("telegram_monitor.coach.gemini.generate", return_value="違和："):
            self.assertIsNone(_coach().premise_check("x", "y", []))

    def test_history_tail_passed_to_prompt(self):
        captured = {}

        def fake(api, model, system, user, **kw):
            captured["user"] = user
            return "OK"
        hist = [{"role": "user", "text": "在嗎"}, {"role": "model", "text": "在的"}]
        with mock.patch("telegram_monitor.coach.gemini.generate", side_effect=fake):
            _coach().premise_check("你吃早餐了嗎", "現在晚上", hist)
        self.assertIn("你吃早餐了嗎", captured["user"])
        self.assertIn("在嗎", captured["user"])             # 對話脈絡有帶進去
        self.assertIn("現在晚上", captured["user"])          # 接地時間事實有帶進去


class WorthPremiseCheckTest(unittest.TestCase):
    """便宜前置門檻：只有可能有可檢查前提的輪次才送審（省 LLM 呼叫）；純陳述/附和跳過。"""

    def test_premise_bearing_pass(self):
        from telegram_monitor import dialogue_intent as di
        for t in ("你吃過早餐了嗎", "你睡了嗎", "你下班了嗎", "現在是早上嗎", "breakfast?", "你早餐吃了嗎"):
            self.assertTrue(di.worth_premise_check(t), t)

    def test_premise_less_skip(self):
        from telegram_monitor import dialogue_intent as di
        for t in ("這篇寫得不錯", "我覺得很有道理", "嗯嗯", "謝謝你", "好喔我知道了", "x", ""):
            self.assertFalse(di.worth_premise_check(t), t)


class PremiseHintTest(unittest.TestCase):
    def test_hint_carries_why(self):
        h = persona.premise_violation_hint("現在是晚上、不是早餐時間")
        self.assertIn("現在是晚上、不是早餐時間", h)
        self.assertIn("先自然地", h)


if __name__ == "__main__":
    unittest.main()
