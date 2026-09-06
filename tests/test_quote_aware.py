"""🔖 引用感知：使用者用 Telegram 回覆/引用功能、指向 bot 自己某則訊息再發問（截圖：引用「這可真特別。」
問「為什麼」）→ 取出被引用那句當 anchor，接地回答、不當新話題。關旗標＝看不到引用、逐位元同現狀。"""

import unittest

from telegram_monitor import monitor, persona


def _upd(text, reply_is_bot=None, reply_text=None, quote_text=None, edited=False):
    msg = {"text": text, "chat": {"id": 1}, "date": 1}
    if reply_text is not None or reply_is_bot is not None:
        msg["reply_to_message"] = {"text": reply_text, "from": {"is_bot": reply_is_bot}}
    if quote_text is not None:
        msg["quote"] = {"text": quote_text}
    return {"edited_message": msg} if edited else {"message": msg}


class QuotedSelfTextTest(unittest.TestCase):
    def test_quotes_bot_message(self):
        self.assertEqual(monitor.quoted_self_text(_upd("為什麼", reply_is_bot=True, reply_text="這可真特別。")),
                         "這可真特別。")

    def test_partial_quote_preferred(self):
        # 部分引用（使用者實際選取那段）優先於整則
        self.assertEqual(monitor.quoted_self_text(
            _upd("為什麼", reply_is_bot=True, reply_text="一整段很長的話，這可真特別。後面還有。", quote_text="這可真特別")),
            "這可真特別")

    def test_reply_to_own_message_not_counted(self):
        # 引用的是對方自己的話（is_bot=False）→ 不算引用 bot
        self.assertEqual(monitor.quoted_self_text(_upd("為什麼", reply_is_bot=False, reply_text="我自己說的")), "")

    def test_no_reply(self):
        self.assertEqual(monitor.quoted_self_text(_upd("為什麼")), "")

    def test_missing_from(self):
        self.assertEqual(monitor.quoted_self_text({"message": {"text": "x", "reply_to_message": {"text": "y"}}}), "")

    def test_edited_message_variant(self):
        self.assertEqual(monitor.quoted_self_text(_upd("為什麼", reply_is_bot=True, reply_text="嗨", edited=True)), "嗨")

    def test_empty_and_garbage(self):
        self.assertEqual(monitor.quoted_self_text({}), "")
        self.assertEqual(monitor.quoted_self_text(None), "")


class QuoteSurvivesCoalescingTest(unittest.TestCase):
    def test_coalesced_keeps_quote_from_last_text_msg(self):
        # 連發合併後（synth=末則文字訊息為底）仍取得回被引用的 bot 句
        group = {"type": "text", "updates": [
            {"update_id": 1, "message": {"text": "嗯", "date": 1, "chat": {"id": 1}}},
            {"update_id": 2, "message": {"text": "為什麼", "date": 2, "chat": {"id": 1},
                                         "reply_to_message": {"text": "這可真特別。", "from": {"is_bot": True}}}},
        ]}
        synth = monitor.build_coalesced_update(group)
        self.assertEqual(monitor.quoted_self_text(synth), "這可真特別。")


class QuotedSelfHintTest(unittest.TestCase):
    def test_hint_carries_quote_and_instruction(self):
        h = persona.quoted_self_hint("這可真特別。")
        self.assertIn("這可真特別。", h)
        self.assertIn("引用你說過的這句", h)
        self.assertIn("別把它當成新話題", h)

    def test_hint_truncates_long_quote(self):
        h = persona.quoted_self_hint("超長" * 200)
        self.assertIn("…", h)
        self.assertLess(len(h), 400)


if __name__ == "__main__":
    unittest.main()
