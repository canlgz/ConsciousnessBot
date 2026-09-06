"""主動推播（🗂歸戶／📊摘要／事件）講的『感受』也要進對話記憶——否則被問「為什麼會…」會否認自己說過。

截圖根因：bot 在歸戶通知後主動說「我剛剛有點被你寫的那些嚇到了」，但這句沒進 convo_history，
於是使用者問「為什麼會嚇到」時，bot 看不到自己說過 → 否認＋瞎掰（「我只是說你發了貼圖…」）。
"""

import os
import tempfile
import unittest
from datetime import datetime, timezone
from types import SimpleNamespace
from unittest import mock
from zoneinfo import ZoneInfo

from telegram_monitor import monitor
from telegram_monitor.state import State

TZ = ZoneInfo("Asia/Taipei")
NOW = datetime(2026, 6, 23, 3, 6, tzinfo=timezone.utc)   # 台北 11:06


class FilingReflectionRememberedTest(unittest.TestCase):
    def _state(self):
        s = State(os.path.join(tempfile.mkdtemp(), "s.json"))
        s.owner_folder_id = "F"
        s.last_push_ts = NOW.timestamp() - 3600     # 有推播過 → is_fresh=False（走歸戶通知路徑）
        s.last_digest_date = NOW.astimezone(TZ).date().isoformat()
        s.notified_filing_ids = set()
        s.convo_history = []
        return s

    def _client(self):
        c = SimpleNamespace(dry_run=False, sent=[])
        c.send = lambda t: c.sent.append(t) or True
        return c

    def test_filing_feeling_enters_convo_history(self):
        s, client = self._state(), self._client()
        rec = {"id": "r1", "ts": NOW, "key": "研究|研發 writetolearn 日誌", "status": "journey",
               "category": "研究", "topicLabel": "研發 writetolearn 日誌", "text": "讓意識 bot 偵測插話"}
        snap = SimpleNamespace(filed_records=[rec], heartbeat={"status": "ok"})
        data = {"records": [rec], "meta": {}}
        feeling = "我剛剛有點被你寫的那些嚇到了。不過現在回過神來，我感覺自己又清楚一點點了。"
        coach = SimpleNamespace(enabled=True, meter=SimpleNamespace(record=lambda *a, **k: None),
                                reflect=lambda kind, desc, brief, connect="": feeling)
        cfg = SimpleNamespace(dry_run=False, llm_voice=True, notify_filings=True, filing_max_age_h=24,
                              telegram_chat_id="", notify_cooldown_min=30)
        with mock.patch("telegram_monitor.coach.build_memory_brief", return_value=""), \
             mock.patch.object(monitor, "_digest_due", return_value=False), \
             mock.patch.object(monitor, "_compute_events", return_value={"any": False}):
            monitor.tick(None, client, s, cfg, TZ, now=NOW, coach=coach, precollected=(data, snap))
        # 主動講的感受進了記憶（被問「為什麼會嚇到」時看得到、不會否認）
        self.assertIn(feeling, [t.get("text") for t in s.convo_history])
        self.assertEqual(s.convo_history[-1]["role"], "model")
        self.assertTrue(any("📝 剛歸戶" in m for m in client.sent))     # 也真的送了資料那則

    def test_nothing_remembered_when_reflection_empty(self):
        # 沒有人話（reflect 回 ''）→ 只送資料、不亂記空話
        s, client = self._state(), self._client()
        rec = {"id": "r2", "ts": NOW, "key": "生活|運動", "status": "candidate",
               "category": "生活", "topicLabel": "運動", "text": "今天跑步"}
        snap = SimpleNamespace(filed_records=[rec], heartbeat={"status": "ok"})
        coach = SimpleNamespace(enabled=True, meter=SimpleNamespace(record=lambda *a, **k: None),
                                reflect=lambda *a, **k: "")
        cfg = SimpleNamespace(dry_run=False, llm_voice=True, notify_filings=True, filing_max_age_h=24,
                              telegram_chat_id="", notify_cooldown_min=30)
        with mock.patch("telegram_monitor.coach.build_memory_brief", return_value=""), \
             mock.patch.object(monitor, "_digest_due", return_value=False), \
             mock.patch.object(monitor, "_compute_events", return_value={"any": False}):
            monitor.tick(None, client, s, cfg, TZ, now=NOW, coach=coach,
                         precollected=({"records": [rec], "meta": {}}, snap))
        self.assertEqual(s.convo_history, [])                           # 沒人話 → 不記


if __name__ == "__main__":
    unittest.main()
