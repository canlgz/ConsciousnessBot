"""🎴🧠 §1.23 STICKER_SENT_MEMORY：bot 送出的貼圖也要有記憶——對話史入帳＋跨重生持久化＋否認句誠實閘。

截圖根因（18:12 bot 送忍者貼圖 → 18:23 問「你知道你傳的貼圖內容嗎」→ bot：「我好像沒有傳貼圖給你耶」
「我剛才只有說文字」）：送出路徑與對話記憶是**兩條不相連的路**——
① 收訊方向有記：使用者傳貼圖 → _remember「（貼圖：emoji——desc）」進 convo_history（monitor.py:1229）；
   送出方向零記錄 → chat LLM 的歷史裡真的只有文字＝它誠實地否認了自己做過的事。
② last_sticker_ts/id/emoji/desc 四欄位「記憶體、重啟歸零」（state.py:160-164）——bot 頻繁死亡重生
   （睡了大概 8 分鐘），一次重生就抹掉 → §0.90 接地 15 分窗失效。
旗標 STICKER_SENT_MEMORY=0＝三件全關＝逐位元同現狀。
"""

import os
import tempfile
import time
import unittest
from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import patch
from zoneinfo import ZoneInfo

from telegram_monitor import monitor
from telegram_monitor.state import State

TZ = ZoneInfo("Asia/Taipei")
NOW = datetime(2026, 7, 12, 10, 23, 0, tzinfo=timezone.utc)   # 台北 18:23（截圖提問時刻）


class FakeClient:
    def __init__(self):
        self.sent, self.stickers, self.dry_run = [], [], False

    def send(self, text):
        self.sent.append(text)
        return True

    def send_sticker(self, fid):
        self.stickers.append(fid)
        return True


def _state(tmp=None):
    s = State(os.path.join(tmp or tempfile.mkdtemp(), "s.json"))
    s.owner_folder_id = "F"
    return s


def _seed_known(s, ts, desc=""):
    s.known_sticker_ids = [{"file_id": "NINJA", "file_unique_id": "u1", "emoji": "🥷",
                            "valence": "neutral", "ts": ts, "desc": desc}]


class SentHistoryTest(unittest.TestCase):
    """① 送出也要進對話史——_record_sticker_sent 是七條送出路的唯一收口。"""

    def test_send_writes_model_turn(self):
        s = _state()
        _seed_known(s, time.time(), desc="一個吊在繩上的忍者")
        with patch.dict(os.environ, {"STICKER_SENT_MEMORY": "1"}):
            monitor._record_sticker_sent(s, "NINJA", time.time())
        turns = [h for h in s.convo_history if h["role"] == "model" and "貼圖" in h["text"]]
        self.assertEqual(len(turns), 1)
        self.assertIn("忍者", turns[0]["text"])            # 有視覺描述就帶進去（據實）

    def test_send_without_desc_uses_emoji(self):
        s = _state()
        _seed_known(s, time.time(), desc="")
        with patch.dict(os.environ, {"STICKER_SENT_MEMORY": "1"}):
            monitor._record_sticker_sent(s, "NINJA", time.time())
        turns = [h for h in s.convo_history if h["role"] == "model" and "貼圖" in h["text"]]
        self.assertEqual(len(turns), 1)
        self.assertIn("🥷", turns[0]["text"])

    def test_flag_off_no_history(self):
        s = _state()
        _seed_known(s, time.time())
        with patch.dict(os.environ, {"STICKER_SENT_MEMORY": "0"}):
            monitor._record_sticker_sent(s, "NINJA", time.time())
        self.assertEqual(s.convo_history, [])              # 旗標關＝逐位元同現狀
        self.assertEqual(s.last_sticker_id, "NINJA")       # 既有欄位更新照舊


class PersistTest(unittest.TestCase):
    """② 四欄位跨重生持久化——重啟不再失憶（§0.90 接地窗不再被重生抹掉）。"""

    def test_roundtrip_keeps_fields(self):
        tmp = tempfile.mkdtemp()
        s = _state(tmp)
        _seed_known(s, time.time(), desc="忍者")
        with patch.dict(os.environ, {"STICKER_SENT_MEMORY": "1"}):
            monitor._record_sticker_sent(s, "NINJA", 1770000000.0)
            s.save()
            s2 = State.load(os.path.join(tmp, "s.json"))
        self.assertEqual(s2.last_sticker_id, "NINJA")
        self.assertEqual(s2.last_sticker_ts, 1770000000.0)
        self.assertEqual(s2.last_sticker_emoji, "🥷")

    def test_flag_off_reset_on_rebirth(self):
        tmp = tempfile.mkdtemp()
        s = _state(tmp)
        _seed_known(s, time.time())
        with patch.dict(os.environ, {"STICKER_SENT_MEMORY": "0"}):
            monitor._record_sticker_sent(s, "NINJA", 1770000000.0)
            s.save()
            s2 = State.load(os.path.join(tmp, "s.json"))
        self.assertEqual(s2.last_sticker_ts, 0)            # 旗標關＝重生歸零＝同現狀
        self.assertIsNone(s2.last_sticker_id)


class DenialGuardTest(unittest.TestCase):
    """③ 否認句誠實閘（§1.13B/§1.20 同構）：11 分鐘前才送過，回覆卻說「我好像沒有傳貼圖」→ 整則替換。"""

    DENIAL = "呃，我剛剛......嗯，我好像沒有傳貼圖給你耶。"

    def _cfg(self, **over):
        base = dict(dry_run=False, telegram_chat_id="", scheduled_promise_enabled=True,
                    promise_emit_enabled=True, promise_sched_ttl_sec=21600, timezone="Asia/Taipei",
                    notify_cooldown_min=30, promise_reply_bridge_enabled=False, promise_ledger_enabled=True,
                    sched_leave_autoarm_enabled=True, deferred_promise_enabled=True,
                    promise_llm_rescue_enabled=False, sticker_llm_rescue_enabled=False,
                    promise_keep_claim_guard_enabled=True, promise_said_ground_enabled=True,
                    bot_self_promise_enabled=False, promise_preempt_enabled=False,
                    sticker_sent_memory_enabled=True, sent_sticker_ground_enabled=True)
        base.update(over)
        return SimpleNamespace(**base)

    def _coach(self, reply_text):
        c = SimpleNamespace(enabled=True, api_key="k", model="m",
                            meter=SimpleNamespace(record=lambda *a, **k: None))
        c.ask = lambda *a, **k: ("chat", None, reply_text)
        c.reply = lambda *a, **k: reply_text
        c.voice_schedule_ack = lambda q, w, h, sticker_hint="": "好。"
        c.voice_promise_ack = lambda q, h: "好。"
        c.judge_timed_request = lambda t: None
        c.judge_sticker_request = lambda t: None
        c.judge_self_promise = lambda t: None
        c.judge_promise_preempt = lambda *a, **k: False
        c.voice_promise_keep = lambda *a, **k: None
        c.voice_greeting = lambda *a, **k: "早安！"
        return c

    SNAP = SimpleNamespace(summary={"total": 1, "last24h": 0, "last7d": 0},
                           funnel={"candidate": 0, "context": 0, "journey": 0, "watch": 0},
                           heartbeat={"status": "ok"}, filed_records=[])
    BOOM = SimpleNamespace(load_embedding_records=lambda *_: None)

    def _run(self, reply_text, sent_ago, cfg=None):
        s = _state()
        now_ts = NOW.timestamp()
        _seed_known(s, now_ts - sent_ago - 5, desc="一個吊在繩上的忍者")
        with patch.dict(os.environ, {"STICKER_SENT_MEMORY": "1"}):
            monitor._record_sticker_sent(s, "NINJA", now_ts - sent_ago)
        s.convo_history = []                               # 只測守門（不靠史）：模擬重生後史被截斷的最壞情況
        cl = FakeClient()
        monitor.handle_message({"message": {"chat": {"id": 1}, "text": "你知道你傳的貼圖內容嗎",
                                            "date": now_ts}},
                               self._coach(reply_text), self.BOOM, {"meta": {}}, self.SNAP,
                               s, cl, cfg or self._cfg(), TZ)
        return "\n".join(cl.sent)

    def test_denial_replaced(self):
        reply = self._run(self.DENIAL, sent_ago=660)       # 11 分鐘前才送過（截圖情境）
        self.assertNotIn("沒有傳貼圖", reply)
        self.assertIn("送過", reply)
        self.assertIn("忍者", reply)                       # 誠實句接地到真送出那張的畫面

    def test_quote_attribution_not_intercepted(self):
        quoted = "你剛剛說我好像沒有傳貼圖給你，其實我有送喔。"
        reply = self._run(quoted, sent_ago=660)
        self.assertIn("你剛剛說", reply)                   # 引用歸屬＝不攔

    def test_no_recent_sticker_no_intercept(self):
        reply = self._run(self.DENIAL, sent_ago=3 * 3600)  # 3 小時前＝窗外，否認不算說謊（別翻舊帳）
        self.assertIn("沒有傳貼圖", reply)

    def test_flag_off_passthrough(self):
        reply = self._run(self.DENIAL, sent_ago=660,
                          cfg=self._cfg(sticker_sent_memory_enabled=False))
        self.assertIn("沒有傳貼圖", reply)                 # 旗標關＝逐位元同現狀

    # ── §1.62（2026-07-22 18:30 截圖＋使用者回饋定案「原來的回答是很不錯的方式」）：問句形與系統歸因形
    # ＝**句級軟更正**——透明推理（查紀錄/承認不確定）保留、只把事實錯的那一句換掉；經典直述否認仍整則替換 ──
    def test_question_form_soft_corrected_reasoning_kept(self):
        reply = self._run("欸，我剛剛有傳貼圖嗎？我這裡顯示我剛剛只是說了一句話。", sent_ago=300)
        self.assertNotIn("有傳貼圖嗎", reply)                # 錯句換掉
        self.assertIn("確實送出了一張貼圖", reply)            # 換成事實句（含挑圖機制＝代表意義）
        self.assertIn("我這裡顯示", reply)                   # 透明推理句保留（使用者肯定的風格）

    def test_system_attribution_soft_corrected_reasoning_kept(self):
        reply = self._run("我能送貼圖，這是真的。但如果我沒說要送，卻突然出現一張，那應該是系統自己送的，我不知道是哪一張耶。",
                          sent_ago=300)
        self.assertNotIn("系統自己送的", reply)              # 行為割裂句換掉
        self.assertIn("確實送出了一張貼圖", reply)
        self.assertIn("我能送貼圖，這是真的。", reply)        # 好的誠實句原樣保留

    def test_user_offer_question_not_intercepted(self):
        # 「你要我傳貼圖嗎」＝邀約、不是否認——不攔
        reply = self._run("你要我傳貼圖嗎？", sent_ago=300)
        self.assertIn("你要我傳貼圖嗎", reply)


class ConfigSyncTest(unittest.TestCase):
    def test_flag_declared_and_env_synced(self):
        import re
        src = open("telegram_monitor/config.py", encoding="utf-8").read()
        self.assertIn("STICKER_SENT_MEMORY", src)
        self.assertIn("sticker_sent_memory_enabled", src)
        env = open(".env.example", encoding="utf-8").read()
        self.assertRegex(env, re.compile(r"^STICKER_SENT_MEMORY=1", re.M))


if __name__ == "__main__":
    unittest.main()
