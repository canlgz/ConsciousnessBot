"""🪞 自我指涉辨識：使用者問的是 bot 自己的哪個面向？優先序 change>experience>state，不被「感覺」劫持。"""

import unittest

from telegram_monitor import selfref


class SelfAspectTest(unittest.TestCase):
    def test_change_wins_over_state_even_with_gan_jue(self):
        # 截圖的關鍵 bug：「你有沒有感覺到自己的變化」含「感覺」，但問的是『我的變化』→ change，不是 state
        self.assertEqual(selfref.self_aspect("你有沒有感覺到自己的變化"), "change")
        self.assertEqual(selfref.self_aspect("你有什麼變化嗎"), "change")
        self.assertEqual(selfref.self_aspect("你變得不太一樣了"), "change")

    def test_change_aspect(self):
        for q in ["你變了沒", "你哪裡不一樣", "這版你是什麼", "你最近更新了什麼"]:
            self.assertEqual(selfref.self_aspect(q), "change", q)

    def test_identity_aspect(self):
        # 身分問句 → identity；實際身分如實交代，change 仍贏過 identity。
        for q in ["你知道自己是誰嗎", "你是誰", "你是什麼", "你是不是只是個 AI", "介紹你自己", "你是語言模型嗎"]:
            self.assertEqual(selfref.self_aspect(q), "identity", q)
        self.assertEqual(selfref.self_aspect("你是什麼版本"), "change")   # 版本＝change，優先序在前

    def test_persona_does_not_deny_ai_identity(self):
        from telegram_monitor import persona
        self.assertIn("不能為維持角色而否認", persona.SOCRATIC_SYSTEM)
        self.assertIn("不否認 AI 身分", persona.identity_user("你是誰"))
        self.assertNotIn("別自稱語言模型", persona.identity_user("你是誰"))

    def test_experience_aspect(self):
        for q in ["你這段日子怎麼活過來的", "你的一生", "你體驗到什麼"]:
            self.assertEqual(selfref.self_aspect(q), "experience", q)

    def test_state_aspect(self):
        for q in ["你現在怎樣", "你好嗎", "你現在有什麼感覺", "你裡面真的有感覺嗎"]:
            self.assertEqual(selfref.self_aspect(q), "state", q)

    def test_future_comm_note_is_not_state(self):
        self.assertIsNone(selfref.self_aspect("下次說感覺的時候要具體一點喔"))

    def test_non_self_is_none(self):
        for q in ["今天天氣如何", "我今天寫了什麼", "幫我看研發日誌", "現在幾點"]:
            self.assertIsNone(selfref.self_aspect(q), q)

    def test_is_about_self(self):
        self.assertTrue(selfref.is_about_self("你有沒有感覺到自己的變化"))
        self.assertTrue(selfref.is_about_self("你現在怎樣"))
        self.assertTrue(selfref.is_about_self("你的內在"))           # 自我線索（is_self_topic）
        self.assertFalse(selfref.is_about_self("今天天氣如何"))


if __name__ == "__main__":
    unittest.main()
