"""🦜 反鸚鵡學舌：偵測「開頭照搬對方上一句」的覆述，並在對話路徑重生成一次。

截圖根因：自陳/狀態回覆被 `_connect_hint` 要求「承接前文」時附上對方原句，LLM 直接把它搬來當開頭
（問「你的內在什麼時候形成感覺」，bot 先吐「所以，是還是不是啊……」＝對方更早那句）。"""

import unittest
from types import SimpleNamespace
from unittest import mock

from telegram_monitor import coach, echo, gemini, persona


class DetectTest(unittest.TestCase):
    def test_flags_leading_parrot(self):
        # 開頭照搬對方上一句（含尾巴語氣詞/標點）＝覆述
        self.assertTrue(echo.is_echo("所以，是還是不是啊……\n\n嗯，我現在活著。", ["所以，是還是不是啊"]))
        self.assertTrue(echo.is_echo("你的內在什麼時候形成感覺？這個嘛……", ["你的內在什麼時候形成感覺"]))

    def test_ignores_genuine_answer(self):
        # 真正在回答（沒照搬對方的話）→ 不算覆述
        self.assertFalse(echo.is_echo("一半是，一半不是。我對你資料的感覺是算出來的。", ["所以是還是不是啊"]))
        self.assertFalse(echo.is_echo("是這樣沒錯，那條線最近又繞回來了。", ["你還記得惠中寺那條嗎"]))

    def test_ignores_short_agreements(self):
        # 短附和（好啊/嗯）不該因為跟對方某個短詞像就被當覆述
        self.assertFalse(echo.is_echo("好啊，那我們繼續。", ["好"]))
        self.assertFalse(echo.is_echo("嗯。", ["嗯嗯"]))

    def test_strip_leading_echo_keeps_substance(self):
        out, stripped = echo.strip_leading_echo("所以，是還是不是啊……\n嗯，我現在活著，有點餓。",
                                                ["所以，是還是不是啊"])
        self.assertTrue(stripped)
        self.assertNotIn("是還是不是", out)
        self.assertIn("我現在活著", out)

    def test_strip_noops_when_all_echo(self):
        # 整句都是覆述（剝完沒東西）→ 原樣退回，不留空訊息
        out, stripped = echo.strip_leading_echo("所以是還是不是啊", ["所以是還是不是啊"])
        self.assertFalse(stripped)
        self.assertEqual(out, "所以是還是不是啊")

    def test_recent_user_texts(self):
        hist = [{"role": "user", "text": "U1"}, {"role": "model", "text": "M1"}, {"role": "user", "text": "U2"}]
        self.assertEqual(echo.recent_user_texts(hist, k=2), ["U2", "U1"])


class CoachRegenTest(unittest.TestCase):
    """coach.reply 偵測到覆述 → 帶 ANTI_ECHO_HINT 重生成一次，回不覆述的那版。"""

    def _coach(self):
        return coach.Coach(SimpleNamespace(gemini_api_key="k", gemini_model="m"))

    def test_reply_regenerates_on_parrot(self):
        c = self._coach()
        calls = []

        def fake_chat(api, model, system, contents, **kw):
            calls.append(system)
            return "所以，是還是不是啊……" if len(calls) == 1 else "一半是一半不是，我對你資料的感覺是算出來的。"

        with mock.patch("telegram_monitor.gemini.generate_chat", side_effect=fake_chat):
            out = c.reply("你的內在什麼時候形成感覺", "brief",
                          [{"role": "user", "text": "所以，是還是不是啊"}])
        self.assertEqual(len(calls), 2)                              # 偵測到覆述 → 重生成一次
        self.assertIn(persona.ANTI_ECHO_HINT, calls[1])             # 第二次帶反覆述提示
        self.assertIn("一半是一半不是", out)                         # 回不覆述的那版
        self.assertNotIn("是還是不是啊", out)

    def test_reply_no_regen_when_clean(self):
        c = self._coach()
        calls = []

        def fake_chat(api, model, system, contents, **kw):
            calls.append(system)
            return "一半是一半不是，那條線最近又繞回來了。"

        with mock.patch("telegram_monitor.gemini.generate_chat", side_effect=fake_chat):
            out = c.reply("所以是還是不是", "brief", [{"role": "user", "text": "所以是還是不是"}])
        self.assertEqual(len(calls), 1)                             # 沒覆述 → 不浪費一次重生成
        self.assertIn("繞回來", out)

    def test_reply_regen_failure_falls_back_to_strip(self):
        c = self._coach()
        seq = iter([
            "所以是還是不是啊……\n我覺得那條線在成形。",   # 第一次：覆述開頭＋實質內容
        ])

        def fake_chat(api, model, system, contents, **kw):
            try:
                return next(seq)
            except StopIteration:
                raise gemini.GeminiError("boom")               # 重生成時炸 → 退回剝開頭

        with mock.patch("telegram_monitor.gemini.generate_chat", side_effect=fake_chat):
            out = c.reply("你怎麼想", "brief", [{"role": "user", "text": "所以是還是不是啊"}])
        self.assertIn("那條線在成形", out)
        self.assertNotIn("是還是不是", out)


if __name__ == "__main__":
    unittest.main()
