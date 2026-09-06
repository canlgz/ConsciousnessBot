"""⏱ 餐別 premise 對不對得上此刻時段：晚上問早餐＝時間上違反常理（bot 有時鐘卻常只盯「我沒有身體」、漏掉時間點）。"""

import unittest

from telegram_monitor import dialogue_intent as di, persona, temporal


class MealPremiseMismatchTest(unittest.TestCase):
    def test_breakfast_at_evening_mismatch(self):
        mm = di.meal_premise_mismatch("你吃過早餐了嗎", temporal.day_part(19))   # 19=晚上
        self.assertEqual(mm["meal"], "早餐")
        self.assertEqual(mm["now_part"], "晚上")

    def test_lunch_at_evening_mismatch(self):
        self.assertIsNotNone(di.meal_premise_mismatch("你吃午餐了嗎", temporal.day_part(19)))

    def test_dinner_at_evening_ok(self):
        self.assertIsNone(di.meal_premise_mismatch("你吃晚餐了嗎", temporal.day_part(19)))

    def test_breakfast_at_morning_ok(self):
        self.assertIsNone(di.meal_premise_mismatch("吃早餐了沒", temporal.day_part(8)))

    def test_no_meal_word(self):
        self.assertIsNone(di.meal_premise_mismatch("現在幾點", temporal.day_part(19)))
        self.assertIsNone(di.meal_premise_mismatch("", temporal.day_part(19)))

    def test_english_meal(self):
        self.assertIsNotNone(di.meal_premise_mismatch("did you have breakfast", temporal.day_part(20)))


class TimePremiseHintTest(unittest.TestCase):
    def test_hint_mentions_meal_and_part_no_digits(self):
        mm = di.meal_premise_mismatch("你吃過早餐了嗎", temporal.day_part(19))
        h = persona.time_premise_hint(mm)
        self.assertIn("早餐", h)
        self.assertIn("晚上", h)
        self.assertFalse(any(c.isdigit() for c in h))   # 用時段字面、不報精確時間數字


if __name__ == "__main__":
    unittest.main()
