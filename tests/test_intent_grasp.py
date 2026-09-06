"""意圖掌握：自我在場窗開著時，**明確的資料/列表問句**不該被自我在場提示帶偏成「談自己的體驗」回避。
截圖：問「完整的學習歷程是哪些」，bot 卻講自己在研發寫下來學習…形成某個形狀（自我體驗獨白），不去列歷程。"""

import os
import tempfile
import unittest
from types import SimpleNamespace
from unittest import mock

from telegram_monitor import monitor
from telegram_monitor.state import State

NOW = 1_700_000_000


def _client():
    c = SimpleNamespace(sent=[], dry_run=False)
    c.send = lambda t: c.sent.append(t) or True
    return c


def _cfg():
    return SimpleNamespace(dry_run=False, telegram_chat_id="", mood_gain=1.0)


class DataQuestionNotSelfBiasedTest(unittest.TestCase):
    def _run(self, text):
        s = State(os.path.join(tempfile.mkdtemp(), "s.json"))
        s.owner_folder_id = "F"
        s.self_topic_ts = NOW                       # 自我在場窗開著（截圖的觸發條件）
        cap = {}

        def fake_ask(q, brief, ctx, hist, mood_hint="", self_presence=False, now_ts=None, **_):
            cap["self_presence"] = self_presence
            cap["evidence_tools"] = _.get("evidence_tools")   # 🚪 記下閘旗標（資料問句應拿到 evidence_tools=True）
            return ("data", "🌳 學習歷程：A、B、C", None)   # 模擬 list_funnel_topics 的列表結果

        coach = SimpleNamespace(
            enabled=True, meter=SimpleNamespace(record=lambda *a, **k: None), ask=fake_ask,
            reply=lambda *a, **k: cap.__setitem__("reply", True) or "（談我自己的體驗…）")
        client = _client()
        update = {"message": {"chat": {"id": 1}, "text": text, "date": NOW}}
        with mock.patch("telegram_monitor.coach.build_memory_brief", return_value=""):
            monitor.handle_message(update, coach, None, {"meta": {}, "records": []}, object(),
                                   s, client, _cfg(), None)
        return cap, client

    def test_journeys_list_question_drops_self_presence(self):
        cap, client = self._run("完整的學習歷程，是哪些？")
        self.assertIn("self_presence", cap)
        self.assertFalse(cap["self_presence"])      # 資料問句 → 不帶自我在場（才會去叫列表工具）
        self.assertTrue(cap["evidence_tools"])      # 🚪 資料問句 → evidence_tools=True（拿完整工具表，能叫列表工具）
        self.assertNotIn("reply", cap)              # 沒走自我在場 reply 回避
        self.assertIn("學習歷程", "".join(client.sent))

    def test_other_data_questions_also_drop_self_presence(self):
        for q in ["我有哪些零散的念頭", "幫我列出候選歷程", "哪些卡住了"]:
            cap, _ = self._run(q)
            self.assertFalse(cap["self_presence"], q)


if __name__ == "__main__":
    unittest.main()
