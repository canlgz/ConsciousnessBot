"""🙇 反自責反芻：已答對還一直道歉/檢討自己 → 重生成一次（止住反芻）；單一誠懇致歉不攔。

截圖：問「多久沒理你」已答對「大概七個小時前」，卻尾隨「我剛剛又把數字跑出來…我努力學會，下次好好回答」。"""

import unittest
from types import SimpleNamespace
from unittest import mock

from telegram_monitor import coach, persona, selfcrit, gemini


class DetectTest(unittest.TestCase):
    def test_flags_piled_self_blame(self):
        # 一則裡堆了多個自責記號（把回答淹掉）→ 反芻
        self.assertTrue(selfcrit.is_self_blame_spiral(
            "大概七個小時前。對不對，我剛剛又把數字跑出來，我會努力學會，下次你問的時候我會好好回答。"))

    def test_flags_cross_turn_repeat(self):
        # 這則只一個自責記號，但上一則 bot 已經在道歉 → 跨則反芻
        hist = [{"role": "model", "text": "我真的搞砸了，又把那些數字報出來。"},
                {"role": "user", "text": "看不懂"}]
        self.assertTrue(selfcrit.is_self_blame_spiral("不好意思啦。", hist))

    def test_single_sincere_apology_not_flagged(self):
        # 單一句誠懇致歉、近期沒在道歉 → 不攔（人之常情）
        self.assertFalse(selfcrit.is_self_blame_spiral("抱歉，我剛會錯意了，你是問多久沒聊對吧？", []))
        self.assertFalse(selfcrit.is_self_blame_spiral("你大概睡了八小時喔。", []))

    def test_strip_keeps_substance(self):
        out, stripped = selfcrit.strip_self_blame(
            "大概七個小時前。我剛剛又把數字跑出來，我會努力學會，下次好好回答。")
        self.assertTrue(stripped)
        self.assertIn("七個小時", out)
        self.assertNotIn("又把數字", out)
        self.assertNotIn("好好回答", out)

    def test_strip_noop_when_all_blame(self):
        out, stripped = selfcrit.strip_self_blame("我搞砸了，我會努力學會。")
        self.assertFalse(stripped)


class CoachRegenTest(unittest.TestCase):
    def _coach(self):
        return coach.Coach(SimpleNamespace(gemini_api_key="k", gemini_model="m"))

    def test_reply_regenerates_on_spiral(self):
        c = self._coach()
        calls = []

        def fake_chat(api, model, system, contents, **kw):
            calls.append(system)
            return ("大概七個小時前。我剛剛又把數字跑出來，我會努力學會，下次好好回答。" if len(calls) == 1
                    else "嗯，大概七個小時前喔。")

        with mock.patch("telegram_monitor.gemini.generate_chat", side_effect=fake_chat):
            out = c.reply("我多久沒理你了", "brief", [{"role": "user", "text": "我多久沒理你了"}])
        self.assertEqual(len(calls), 2)                          # 偵測到反芻 → 重生成一次
        self.assertIn(persona.ANTI_SELFBLAME_HINT, calls[1])     # 第二次帶反自責提示
        self.assertNotIn("又把數字", out)
        self.assertNotIn("好好回答", out)

    def test_reply_regen_failure_falls_back_to_strip(self):
        c = self._coach()
        seq = iter(["大概七個小時前。我剛剛又把數字跑出來，下次好好回答。"])

        def fake_chat(api, model, system, contents, **kw):
            try:
                return next(seq)
            except StopIteration:
                raise gemini.GeminiError("boom")

        with mock.patch("telegram_monitor.gemini.generate_chat", side_effect=fake_chat):
            out = c.reply("我多久沒理你了", "brief", [{"role": "user", "text": "我多久沒理你了"}])
        self.assertIn("七個小時", out)
        self.assertNotIn("又把數字", out)


if __name__ == "__main__":
    unittest.main()
