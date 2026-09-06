"""自我在場單一化（Wave 4）：自我在場/動作狀態跨重生持久化；自我在場窗只在「真的還在說我」時續期
（飄走的閒聊讓窗自然衰減，不再無限續期）。"""

import os
import tempfile
import unittest
from types import SimpleNamespace
from unittest import mock

from telegram_monitor import monitor
from telegram_monitor.state import State

NOW = 1_700_000_000


class PersistenceTest(unittest.TestCase):
    def test_self_presence_state_survives_restart(self):
        p = os.path.join(tempfile.mkdtemp(), "s.json")
        s = State(p)
        s.self_topic_ts = 123.0
        s.last_liked = {"emoji": "👍", "topic": "研發 X 線", "ts": 5.0}
        s.last_sent_reaction = {"emoji": "🥰", "to": "等你喔", "ts": 6.0}
        s.save()
        s2 = State.load(p)
        self.assertEqual(s2.self_topic_ts, 123.0)            # 跨重生不忘「我們還在說我」
        self.assertEqual(s2.last_liked["topic"], "研發 X 線")  # 跨重生記得「你剛讚我哪則」
        self.assertEqual(s2.last_sent_reaction["emoji"], "🥰")  # 跨重生記得「我剛點了什麼情緒」


class WindowDecayTest(unittest.TestCase):
    def _client(self):
        c = SimpleNamespace(sent=[], dry_run=False)
        c.send = lambda t: c.sent.append(t) or True
        return c

    def _coach(self):
        return SimpleNamespace(enabled=True, meter=SimpleNamespace(record=lambda *a, **k: None),
                               reply=lambda *a, **k: "（回覆）嗯，我在。")

    def _run(self, text, self_topic_ts):
        s = State(os.path.join(tempfile.mkdtemp(), "s.json"))
        s.owner_folder_id = "F"
        s.self_topic_ts = self_topic_ts
        client = self._client()
        cfg = SimpleNamespace(dry_run=False, telegram_chat_id="", mood_gain=1.0)
        update = {"message": {"chat": {"id": 1}, "text": text, "date": NOW}}
        with mock.patch("telegram_monitor.coach.build_memory_brief", return_value=""):
            monitor.handle_message(update, self._coach(), None, {"meta": {}, "records": []}, object(),
                                   s, client, cfg, None)
        return s, client

    def test_drifted_chatter_does_not_extend_window(self):
        # 窗內（1 分鐘前談過我）但這句飄走了（不是在說我、也不是查資料）→ 仍走在場分支回，
        # 但**不刷新** self_topic_ts → 窗自然衰減，不會被無關閒聊無限續期。
        s, client = self._run("那天氣呢", NOW - 60)
        self.assertEqual(s.self_topic_ts, NOW - 60)          # 沒被續期
        self.assertTrue(client.sent)                          # 在場分支照常回（延續感仍在）


if __name__ == "__main__":
    unittest.main()
