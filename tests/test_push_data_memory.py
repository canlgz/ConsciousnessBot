"""🗂 §1.59 資料推播也入對話史（PUSH_DATA_MEMORY）：bot 不再否認自己剛發的「📝 剛歸戶」。

截圖根因（09:46–09:57）：09:46 bot 主動推「📝 剛歸戶（1 則記寫）・〈閱讀｜讀誦經書〉」＋一句人話感受；
09:57 使用者說「你不是剛剛告訴我歸戶的訊息」→ bot：「我剛剛沒有告訴你歸戶的訊息耶。」＝否認自己
11 分鐘前發的訊息。機制面：tick() 的推播一律「先資料、後人話」兩則——**人話**那則早已入記憶
（test_proactive_memory 的修），但**資料**那則（format_filings/format_digest/format_events）從未
_remember ＝§1.23「送出方向零記憶」的同構第三次現形（貼圖→人話→資料）。convo_history 裡只有
「又把第一、第二品讀完…」而沒有任何帶「歸戶」字樣的訊息 → LLM 視角裡它真的沒說過 → 誠實地否認＋
「你發現了什麼」（明明在指剛推播的記寫）也接不到指涉、只能亂猜反問。

§1.59：四個資料送出點（歸戶通知/上線摘要/每日摘要/事件推播）成功後把資料訊息也 _remember（model 角色
本就截 200 字＝長摘要只留頭、標籤字樣「剛歸戶/摘要」必在）。旗標兩層分離：config 預設 True／monitor 端
getattr 預設 False＝不記＝逐位元同現狀（test_proactive_memory 的空史斷言不受影響）。全 stub、零網路。
"""

import os
import re
import tempfile
import unittest
from datetime import datetime, timezone
from types import SimpleNamespace
from unittest import mock
from zoneinfo import ZoneInfo

from telegram_monitor import monitor
from telegram_monitor.state import State

TZ = ZoneInfo("Asia/Taipei")
NOW = datetime(2026, 7, 20, 1, 46, tzinfo=timezone.utc)   # 台北 09:46（截圖時刻）

REC = {"id": "r1", "ts": NOW, "key": "生活|閱讀 讀誦經書", "status": "journey",
       "category": "生活", "topicLabel": "閱讀｜讀誦經書", "text": "又把第一、第二品讀完一輪"}


class _Base(unittest.TestCase):
    def _state(self, fresh=False):
        s = State(os.path.join(tempfile.mkdtemp(), "s.json"))
        s.owner_folder_id = "F"
        if not fresh:
            s.last_push_ts = NOW.timestamp() - 3600
            s.last_digest_date = NOW.astimezone(TZ).date().isoformat()
        s.notified_filing_ids = set()
        s.convo_history = []
        return s

    def _client(self):
        c = SimpleNamespace(dry_run=False, sent=[])
        c.send = lambda t: c.sent.append(t) or True
        return c

    def _coach(self, feeling="讀完的節奏感覺很穩呢。"):
        return SimpleNamespace(enabled=True,
                               meter=SimpleNamespace(record=lambda *a, **k: None, usd_twd=32.0),
                               reflect=lambda kind, desc, brief, connect="": feeling)

    def _cfg(self, push_mem=True):
        base = dict(dry_run=False, llm_voice=True, notify_filings=True, filing_max_age_h=24,
                    telegram_chat_id="", notify_cooldown_min=30)
        if push_mem is not None:
            base["push_data_memory_enabled"] = push_mem
        return SimpleNamespace(**base)

    def _tick_filing(self, s, client, cfg, feeling="讀完的節奏感覺很穩呢。"):
        snap = SimpleNamespace(filed_records=[REC], heartbeat={"status": "ok"})
        data = {"records": [REC], "meta": {}}
        with mock.patch("telegram_monitor.coach.build_memory_brief", return_value=""), \
             mock.patch.object(monitor, "_digest_due", return_value=False), \
             mock.patch.object(monitor, "_compute_events", return_value={"any": False}):
            monitor.tick(None, client, s, cfg, TZ, now=NOW, coach=self._coach(feeling),
                         precollected=(data, snap))


class FilingDataRememberedTest(_Base):
    def test_data_message_enters_history_before_feeling(self):
        # 截圖 case：之後被問「你不是剛剛告訴我歸戶的訊息」時，史裡真的有帶「剛歸戶」的那則＝不再否認
        s, client = self._state(), self._client()
        self._tick_filing(s, client, self._cfg())
        texts = [t.get("text") or "" for t in s.convo_history]
        self.assertTrue(any("剛歸戶" in t for t in texts), texts)
        i_data = next(i for i, t in enumerate(texts) if "剛歸戶" in t)
        i_feel = next(i for i, t in enumerate(texts) if "節奏" in t)
        self.assertLess(i_data, i_feel)                              # 先資料、後人話＝史序同送出序
        self.assertTrue(all(t.get("role") == "model" for t in s.convo_history))

    def test_data_remembered_even_without_reflection(self):
        # 人話生成失敗（reflect 回 ''）→ 資料那則仍該入史（送出是事實，不因沒感想而失憶）
        s, client = self._state(), self._client()
        self._tick_filing(s, client, self._cfg(), feeling="")
        self.assertTrue(any("剛歸戶" in (t.get("text") or "") for t in s.convo_history))

    def test_flag_off_bitwise_data_forgotten(self):
        # 【消融】旗標關/缺席＝同現狀：資料那則不入史（截圖的「否認歸戶訊息」行為保留）
        for pm in (False, None):
            s, client = self._state(), self._client()
            self._tick_filing(s, client, self._cfg(push_mem=pm))
            self.assertFalse(any("剛歸戶" in (t.get("text") or "") for t in s.convo_history), str(pm))
            self.assertTrue(any("節奏" in (t.get("text") or "") for t in s.convo_history), str(pm))   # 人話照舊入史


class DigestDataRememberedTest(_Base):
    SNAP = SimpleNamespace(filed_records=[], heartbeat={"status": "ok"},
                           summary={"total": 3, "last24h": 1, "last7d": 2, "streak": 39,
                                    "media": {}, "last_write": None, "active_explorations": 0},
                           funnel={"candidate": 1, "context": 1, "journey": 1, "watch": 0},
                           gaps=[], formed_journeys=[], formed_contexts=[], near_upgrade_sig={})

    def test_daily_digest_body_remembered(self):
        s, client = self._state(), self._client()
        with mock.patch("telegram_monitor.coach.build_memory_brief", return_value=""), \
             mock.patch.object(monitor, "_digest_due", return_value=True), \
             mock.patch.object(monitor, "_compute_events", return_value={"any": False}):
            monitor.tick(None, client, s, self._cfg(), TZ, now=NOW, coach=self._coach(),
                         precollected=({"records": [], "meta": {}}, self.SNAP))
        self.assertTrue(client.sent)                                  # 摘要真的送了
        body_head = client.sent[0][:40]
        self.assertTrue(any((t.get("text") or "").startswith(body_head) for t in s.convo_history))


class ConfigTest(unittest.TestCase):
    def test_config_synced(self):
        src = open("telegram_monitor/config.py", encoding="utf-8").read()
        self.assertIn("PUSH_DATA_MEMORY", src)
        self.assertIn("push_data_memory_enabled", src)
        env = open(".env.example", encoding="utf-8").read()
        self.assertRegex(env, re.compile(r"^PUSH_DATA_MEMORY=1", re.M))
        self.assertIn("PUSH_DATA_MEMORY", open("README.md", encoding="utf-8").read())

    def test_getattr_defaults_false(self):
        self.assertFalse(getattr(SimpleNamespace(), "push_data_memory_enabled", False))


if __name__ == "__main__":
    unittest.main()
