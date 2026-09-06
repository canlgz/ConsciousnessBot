"""gemini body 組裝測試（mock requests，不碰網路）——確保 2.5 思考被關閉。"""

import unittest
from unittest import mock

from telegram_monitor import gemini


class _FakeResp:
    status_code = 200

    def json(self):
        return {"candidates": [{"content": {"parts": [{"text": "ok"}]}}]}


class GeminiBodyTest(unittest.TestCase):
    def _capture(self, model):
        captured = {}

        def fake_post(url, json=None, timeout=None):
            captured["url"] = url
            captured["body"] = json
            return _FakeResp()

        with mock.patch("telegram_monitor.gemini.requests.post", side_effect=fake_post):
            out = gemini.generate("KEY", model, "sys", "hi")
        self.assertEqual(out, "ok")
        return captured["body"]

    def test_thinking_disabled_for_2_5(self):
        gc = self._capture("gemini-2.5-flash")["generationConfig"]
        self.assertEqual(gc.get("thinkingConfig"), {"thinkingBudget": 0})

    def test_no_thinking_field_for_non_2_5(self):
        gc = self._capture("gemini-3.5-flash")["generationConfig"]
        self.assertNotIn("thinkingConfig", gc)

    def test_truncated_output_trimmed_to_last_complete_sentence(self):
        # finishReason=MAX_TOKENS（被截在半句）→ 退回最後一個完整句尾，永不吐半句（截圖「沒說完」根因）
        class Trunc:
            status_code = 200

            def json(self):
                return {"candidates": [{"content": {"parts": [
                    {"text": "第一句講完了。第二句才講一半就被切"}]}, "finishReason": "MAX_TOKENS"}]}

        with mock.patch("telegram_monitor.gemini.requests.post", return_value=Trunc()):
            out = gemini.generate("KEY", "gemini-2.5-flash", "sys", "hi")
        self.assertEqual(out, "第一句講完了。")

    def test_truncated_at_ellipsis_backs_off_past_dangling(self):
        # 截斷剛好停在省略號（『可能跟你…』＝截斷點、非刻意收尾）→ 退回前一個硬句尾，不留斷尾。
        class Trunc:
            status_code = 200

            def json(self):
                return {"candidates": [{"content": {"parts": [
                    {"text": "第一句講完了。這只是假設啦，可能跟你…"}]}, "finishReason": "MAX_TOKENS"}]}

        with mock.patch("telegram_monitor.gemini.requests.post", return_value=Trunc()):
            out = gemini.generate("KEY", "gemini-2.5-flash", "sys", "hi")
        self.assertEqual(out, "第一句講完了。")

    def test_complete_output_untouched(self):
        # 正常結束（無 MAX_TOKENS）→ 原樣不動
        class Done:
            status_code = 200

            def json(self):
                return {"candidates": [{"content": {"parts": [{"text": "講完整了喔"}]}, "finishReason": "STOP"}]}

        with mock.patch("telegram_monitor.gemini.requests.post", return_value=Done()):
            self.assertEqual(gemini.generate("KEY", "gemini-2.5-flash", "sys", "hi"), "講完整了喔")

    def test_empty_parts_raises(self):
        class Empty:
            status_code = 200

            def json(self):
                return {"candidates": [{"content": {"parts": []}, "finishReason": "MAX_TOKENS"}]}

        with mock.patch("telegram_monitor.gemini.requests.post", return_value=Empty()):
            with self.assertRaises(gemini.GeminiError):
                gemini.generate("KEY", "gemini-2.5-flash", "sys", "hi")


if __name__ == "__main__":
    unittest.main()
