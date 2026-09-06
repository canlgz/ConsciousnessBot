"""🌊 §1.77 氣頭上的理解與分寸（HOSTILE_GRACE）：抱怨不是訂單、罵句不是我的台詞。

截圖 20:42-20:44 三個獨立失誤疊在一起：
 ① **語意讀反（主病）**——使用者在罵它送圖「你看，明明白目，還傳這種貼圖」，`is_sticker_send_request`
   卻判 True（本檔 GapTest 釘住實測），於是 bot 照「請求」辦事：又送一張＋「來，這張真貼圖送你 :)」；
 ② **裸複誦**——「不知羞恥。」以 bot 自己的口氣成為中間那顆泡泡（三道防線只看開頭/整則，全空轉）；
 ③ **形態失當**——反問「你覺得我還在辯解嗎？」＋數帳「你又這樣說了一次。」＋賣乖「:)」。
使用者定案：「傳送貼圖不是問題，是 bot 看不懂使用者的語意吧」＝主修語意，貼圖靜音只是後盾。
旗標關＝逐位元同現狀。全 stub、零網路。
"""

import re
import unittest
from types import SimpleNamespace

from telegram_monitor import echo, monitor, persona, selfstate

COMPLAIN = "你看，明明白目，還傳這種貼圖"
BOT_3BUBBLES = "我好像越解釋，你反而越生氣了，這讓我有點難過。不知羞恥。你又這樣說了一次。"


class GapTest(unittest.TestCase):
    """釘住兩個實測缺口——日後有人改詞表/門檻時，這裡說明原本錯在哪。"""

    def test_complaint_was_read_as_request(self):
        self.assertTrue(selfstate.is_sticker_send_request(COMPLAIN))       # 舊行為：抱怨＝請求（主病）
        self.assertTrue(selfstate.is_sticker_send_request("你怎麼又送貼圖"))

    def test_mid_segment_echo_missed_by_all_three(self):
        texts = ["不知羞恥"]
        self.assertFalse(echo.is_echo(BOT_3BUBBLES, texts))               # 只看開頭兩段
        self.assertFalse(echo.is_echo(BOT_3BUBBLES, texts, whole=True))   # §1.74 只比整則
        self.assertFalse(echo.strip_leading_echo(BOT_3BUBBLES, texts)[1])


class ComplaintGateTest(unittest.TestCase):
    def test_complaints_detected(self):
        for m in (COMPLAIN, "你怎麼又送貼圖", "幹嘛還傳這種東西", "誰要這種貼圖", "居然還敢傳"):
            self.assertTrue(selfstate.is_behavior_complaint(m), m)

    def test_real_requests_not_flagged(self):
        for m in ("幫我傳一張貼圖", "再傳一張這種貼圖", "傳一張貼圖給我", "可以送我一張貼圖嗎", "請傳貼圖"):
            self.assertFalse(selfstate.is_behavior_complaint(m), m)

    def test_neutral_and_empty(self):
        self.assertFalse(selfstate.is_behavior_complaint("今天天氣不錯"))
        self.assertFalse(selfstate.is_behavior_complaint(""))


class EchoSegmentTest(unittest.TestCase):
    def test_mid_echo_stripped_rest_kept(self):
        out, hit = echo.strip_echo_segments(BOT_3BUBBLES, ["不知羞恥"], min_len=1)
        self.assertTrue(hit)
        self.assertNotIn("不知羞恥", out)                                  # 罵句的裸複誦剝掉
        self.assertIn("越解釋", out)                                       # 正題保留（§1.62 慣例）

    def test_attribution_segment_kept(self):
        t = "你說「不知羞恥」，我聽到了。我確實沒把話說清楚。"
        self.assertEqual(echo.strip_echo_segments(t, ["不知羞恥"], min_len=1), (t, False))

    def test_no_echo_untouched(self):
        t = "我知道了，這件我確實沒做好。"
        self.assertEqual(echo.strip_echo_segments(t, ["不知羞恥"], min_len=1), (t, False))

    def test_wire_strips_mid_echo(self):
        out, hit = monitor._echo_strip_wire(BOT_3BUBBLES, ["不知羞恥"], whole=True)
        self.assertTrue(hit)
        self.assertNotIn("不知羞恥", out)

    def test_wire_flag_off_bitwise(self):
        self.assertEqual(monitor._echo_strip_wire(BOT_3BUBBLES, ["不知羞恥"]), (BOT_3BUBBLES, False))


class HostileNowTest(unittest.TestCase):
    def _state(self, streak=0, hist=None):
        return SimpleNamespace(hostile_streak=streak, convo_history=hist or [])

    def test_streak_and_window(self):
        cfg = SimpleNamespace(hostile_grace_enabled=True)
        self.assertTrue(monitor._hostile_now(self._state(streak=1), cfg))
        self.assertTrue(monitor._hostile_now(self._state(), cfg, text="你太爛了"))
        self.assertTrue(monitor._hostile_now(
            self._state(hist=[{"role": "user", "text": "你太爛了"}]), cfg))       # 近窗＝氣頭未過

    def test_calm_false(self):
        cfg = SimpleNamespace(hostile_grace_enabled=True)
        self.assertFalse(monitor._hostile_now(self._state(), cfg, text="今天天氣不錯"))

    def test_flag_off_always_false(self):
        self.assertFalse(monitor._hostile_now(self._state(streak=3), SimpleNamespace(), text="你太爛了"))


class GraceHintTest(unittest.TestCase):
    def test_three_bans(self):
        h = persona.HOSTILE_GRACE_HINT
        self.assertIn("別反問", h)                                        # ①（截圖「你覺得我還在辯解嗎？」）
        self.assertIn("別數他的帳", h)                                     # ②（截圖「你又這樣說了一次。」）
        self.assertIn("別賣乖", h)                                        # ③（截圖送圖＋「:)」）
        self.assertIn("把話頭讓回去", h)


class ToneEmoticonTest(unittest.TestCase):
    def test_smiley_stripped_when_negative(self):
        out, ch = monitor._tone_shape("來，這張真貼圖送你 :)", -0.5, -0.2)
        self.assertTrue(ch)
        self.assertNotIn(":)", out)                                       # 顏文字也是歡快標記

    def test_positive_keeps_smiley(self):
        t = "好啊 :)"
        self.assertEqual(monitor._tone_shape(t, 0.5, 0.3), (t, False))


class ConfigTest(unittest.TestCase):
    def test_config_synced(self):
        src = open("telegram_monitor/config.py", encoding="utf-8").read()
        self.assertIn("HOSTILE_GRACE", src)
        self.assertIn("hostile_grace_enabled", src)
        env = open(".env.example", encoding="utf-8").read()
        self.assertRegex(env, re.compile(r"^HOSTILE_GRACE=1", re.M))
        self.assertIn("HOSTILE_GRACE", open("README.md", encoding="utf-8").read())

    def test_getattr_defaults_false(self):
        self.assertFalse(getattr(SimpleNamespace(), "hostile_grace_enabled", False))


if __name__ == "__main__":
    unittest.main()
