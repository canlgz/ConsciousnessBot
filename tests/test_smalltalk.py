"""🗣️ 純附和/確認（是啊/對/嗯/沒錯）走純對話接話，**不進 function-calling**——
否則 LLM 可能把它誤抓去 overall_stats 吐 📊 報表（截圖 bug：bot 認錯後使用者回「是啊」又被回一堆數字）。"""

import os
import tempfile
import unittest
from types import SimpleNamespace
from unittest import mock

from telegram_monitor import intent, monitor, referent, selfstate
from telegram_monitor.state import State

NOW = 1_700_000_000


class DetectTest(unittest.TestCase):
    def test_matches_backchannels(self):
        for q in ["是啊", "是的", "對", "對啊", "嗯", "嗯嗯", "沒錯", "好", "好的", "ok", "OK",
                  "可以", "了解", "真的", "真的假的", "哈哈", "哈哈哈哈", "喔喔", "也是", "是啊！", "對～"]:
            self.assertTrue(selfstate.is_backchannel(q), q)

    def test_matches_praise(self):
        # 🎉 讚美/肯定（截圖：「這麼厲害....」）＝對話反應 → 純對話接住，不開工具吐 📊
        for q in ["這麼厲害....", "好厲害", "太強了！", "你好棒喔", "了不起", "佩服", "太神了", "好讚"]:
            self.assertTrue(selfstate.is_backchannel(q), q)

    def test_ignores_real_questions_and_content(self):
        # 帶疑問詞的真問句、或附和後接實質內容、或含相似字但非讚美 → 不是純附和（要照常處理/走工具）
        for q in ["幾筆？", "多少筆", "這些歸到哪", "你現在怎樣", "為什麼", "是嗎？", "好嗎",
                  "對，幫我看今天寫了什麼", "嗯那學習歷程有哪些", "完整的學習歷程是哪些",
                  "你厲害嗎", "勉強可以", "我要買棒球"]:
            self.assertFalse(selfstate.is_backchannel(q), q)


class RouteTest(unittest.TestCase):
    def _kind(self, t):
        return intent.resolve(t, referent.Referent()).kind

    def test_backchannel_routes_to_smalltalk(self):
        for q in ["是啊", "對", "嗯", "沒錯", "好", "可以"]:
            self.assertEqual(self._kind(q), "smalltalk", q)

    def test_real_short_question_still_tools(self):
        self.assertEqual(self._kind("幾筆？"), "fact_or_chat")    # 真問句 → 照常走工具

    def test_farewell_still_wins_over_smalltalk(self):
        self.assertEqual(self._kind("懂了，謝謝"), "farewell")    # 收尾性的理解語仍由更上位的 farewell 接走


class HandleTest(unittest.TestCase):
    def test_backchannel_uses_plain_reply_not_tools(self):
        # 「是啊」（自我窗沒開）→ 純對話 coach.reply；誤走 function-calling 才會呼叫 coach.ask → 讓它炸。
        s = State(os.path.join(tempfile.mkdtemp(), "s.json"))
        s.owner_folder_id = "F"
        cap = {}

        def fake_reply(q, brief, hist, mood_hint="", now_ts=None, self_presence=False, extra_system=""):
            cap["reply"] = True
            return "嗯，我在——你說。"

        def boom_ask(*a, **k):
            raise AssertionError("純附和不該開 function-calling 工具")

        coach = SimpleNamespace(enabled=True, meter=SimpleNamespace(record=lambda *a, **k: None),
                                reply=fake_reply, ask=boom_ask)
        client = SimpleNamespace(sent=[], dry_run=False, send=lambda t: client.sent.append(t) or True)
        update = {"message": {"chat": {"id": 1}, "text": "是啊", "date": NOW}}
        with mock.patch("telegram_monitor.coach.build_memory_brief", return_value=""):
            monitor.handle_message(update, coach, None, {"meta": {}, "records": []}, object(),
                                   s, client, SimpleNamespace(dry_run=False, telegram_chat_id="", mood_gain=1.0), None)
        self.assertTrue(cap.get("reply"))
        self.assertIn("我在", "".join(client.sent))


if __name__ == "__main__":
    unittest.main()
