"""🧵 §1.54 連續回覆的同組意識：致意句去重（REPLY_ACK_DEDUP）。

截圖根因（10:41–10:42）：使用者連發「廢話一堆」「除非你能夠證明給我看」→ bot 的連續/巢狀回覆各自生成，
一分鐘內送出「嗯，我明白了。」「嗯... 我明白，光憑…」「我明白。」——重複的致意開場讓使用者說
「不是將連續訊息串視為同一組對話」。機制面：沒有任何防線管「剛剛才說過的**純致意句**」（§1.49 只治
插話殘句逐字重播且 ≥5 字、§1.41 只治兌現句、§1.21/1.22 只治自陳措辭）。

§1.54：互動出口（_say）對**純致意泡泡**（嗯／我明白（了）／我知道了／我懂／好／了解／收到…全句錨、
正規化到語意家族）做去重——比對源＝近 150 秒的 model 回覆（handle_message stash 進 _TURN；巢狀輪
自己會重 stash）＋本則稍早的泡泡（同輪內重複也擋）。有實質內容的句子（「嗯... 我明白，光憑我說感覺
會怎樣你很難相信」）不是純致意＝不動；全部被刪光＝保留第一顆（寧可重複、不可失語）。
旗標兩層分離：config 預設 True／monitor 端 getattr 預設 False＝不 stash＝_say 恆 no-op＝逐位元同現狀。
全 stub、零網路。
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
NOW = datetime(2026, 7, 19, 2, 42, 0, tzinfo=timezone.utc)   # 台北 10:42（截圖時刻）


# ── 單元：致意句正規化/判定 ─────────────────────────────────────────────────
class AckCanonTest(unittest.TestCase):
    def test_ack_bubbles_hit(self):
        for t in ("嗯，我明白了。", "我明白。", "嗯...", "嗯。", "我知道了。", "好。", "好的",
                  "了解。", "我懂了", "收到。", "嗯，明白。"):
            self.assertTrue(monitor._is_ack_bubble(t), t)

    def test_contentful_not_ack(self):
        for t in ("嗯... 我明白，光憑我說「感覺」會怎樣，你很難真的相信。",
                  "你這句話，我好像已經聽過三次了。",
                  "要證明給你看，這對我來說有點難。", "收到你的挑戰了。", "", None):
            self.assertFalse(monitor._is_ack_bubble(t), repr(t))

    def test_family_canon_merges_variants(self):
        # 我明白了／我明白／明白＝同一家族（別靠「了」字差異躲過去重）
        self.assertEqual(monitor._ack_canon("嗯，我明白了。"), monitor._ack_canon("我明白。"))
        self.assertEqual(monitor._ack_canon("我知道了"), monitor._ack_canon("知道了。"))


class AckDedupTest(unittest.TestCase):
    def test_within_turn_dup_dropped(self):
        # 同輪內：開頭「嗯，我明白了。」＋結尾「我明白。」＝同家族 → 後者刪；實質內容句不動
        bubbles = ["嗯，我明白了。", "嗯... 我明白，光憑我說感覺會怎樣，你很難真的相信。", "我明白。"]
        out = monitor._ack_dedup(bubbles, [])
        self.assertEqual(out, bubbles[:2])

    def test_cross_turn_dup_dropped(self):
        # 跨輪：上一輪剛說過「嗯，我明白了。」→ 這輪的「我明白。」刪、內容句留
        out = monitor._ack_dedup(["我明白。", "要證明給你看，這對我來說有點難。"], ["嗯，我明白了。"])
        self.assertEqual(out, ["要證明給你看，這對我來說有點難。"])

    def test_recent_contentful_does_not_seed(self):
        # 上一輪是**有內容**的明白句（不是純致意）→ 不當種子：這輪第一次的純致意照常保留
        out = monitor._ack_dedup(["嗯，我明白了。", "內容句。"], ["嗯，我明白你為什麼會懷疑。"])
        self.assertEqual(out, ["嗯，我明白了。", "內容句。"])

    def test_all_dropped_keeps_first(self):
        # 整則只有一顆重複致意 → 保留第一顆（寧可重複、不可失語）
        out = monitor._ack_dedup(["我明白。"], ["嗯，我明白了。"])
        self.assertEqual(out, ["我明白。"])

    def test_stash_none_noop(self):
        bubbles = ["嗯，我明白了。", "我明白。"]
        self.assertEqual(monitor._ack_dedup(bubbles, None), bubbles)   # 旗標關＝stash 缺席＝位元不變


# ── 整合：handle_message 兩輪（150 秒窗）────────────────────────────────────
class IntegrationTest(unittest.TestCase):
    class Cl:
        def __init__(self): self.sent, self.dry_run = [], False
        def send(self, t): self.sent.append(t); return True

    def _cfg(self, dedup=True):
        base = dict(dry_run=False, telegram_chat_id="", scheduled_promise_enabled=True,
                    promise_emit_enabled=True, promise_sched_ttl_sec=21600, timezone="Asia/Taipei",
                    notify_cooldown_min=30, promise_reply_bridge_enabled=False, promise_ledger_enabled=True,
                    sched_leave_autoarm_enabled=True, deferred_promise_enabled=True,
                    promise_llm_rescue_enabled=False, sticker_llm_rescue_enabled=False,
                    promise_keep_claim_guard_enabled=True, promise_said_ground_enabled=True,
                    bot_self_promise_enabled=False, promise_preempt_enabled=False,
                    sticker_sent_memory_enabled=True, send_stickers=True,
                    sticker_fakesend_guard_enabled=False, recall_ground_guard_enabled=False,
                    selfshare_reason_ground_enabled=False, wake_projection_guard_enabled=False,
                    user_habit_ground_enabled=False, self_feel_condense_enabled=False,
                    promise_deliver_content_enabled=False, mood_coord_report_enabled=False)
        if dedup is not None:
            base["reply_ack_dedup_enabled"] = dedup
        return SimpleNamespace(**base)

    def _coach(self, voice):
        c = SimpleNamespace(enabled=True, api_key="k", model="m",
                            meter=SimpleNamespace(record=lambda *a, **k: None))
        c.ask = lambda *a, **k: ("chat", None, voice)
        c.reply = lambda *a, **k: voice
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
    BOOM = SimpleNamespace(load_embedding_records=lambda *_: [])

    def _run_two_turns(self, cfg):
        s = State(os.path.join(tempfile.mkdtemp(), "s.json"))
        s.owner_folder_id = "F"
        cl1, cl2 = self.Cl(), self.Cl()
        # _remember 的 model ts 用牆鐘 time.time()——釘住牆鐘＝turn2 的 150 秒窗計算可重現
        with mock.patch.object(monitor.time, "time", lambda: NOW.timestamp() + 5):
            monitor.handle_message({"message": {"chat": {"id": 1}, "text": "廢話一堆", "date": NOW.timestamp()}},
                                   self._coach("嗯，我明白了。"), self.BOOM, {"meta": {}, "records": []},
                                   self.SNAP, s, cl1, cfg, TZ)
        with mock.patch.object(monitor.time, "time", lambda: NOW.timestamp() + 35):
            monitor.handle_message({"message": {"chat": {"id": 1}, "text": "除非你能夠證明給我看",
                                                "date": NOW.timestamp() + 30}},
                                   self._coach("我明白。要證明給你看，這對我來說有點難。"), self.BOOM,
                                   {"meta": {}, "records": []}, self.SNAP, s, cl2, cfg, TZ)
        return cl1, cl2

    def test_second_turn_drops_repeated_ack(self):
        cl1, cl2 = self._run_two_turns(self._cfg())
        self.assertIn("嗯，我明白了。", cl1.sent)             # 第一輪照常
        self.assertNotIn("我明白。", cl2.sent)                # 30 秒後的第二輪：重複致意刪掉
        self.assertTrue(any("要證明給你看" in b for b in cl2.sent))   # 實質內容照送

    def test_flag_off_bitwise_repeat_preserved(self):
        for dedup in (False, None):
            cl1, cl2 = self._run_two_turns(self._cfg(dedup=dedup))
            self.assertIn("我明白。", cl2.sent, str(dedup))    # 【消融】同現狀：截圖的重複照舊


# ── 同步 ───────────────────────────────────────────────────────────────────
class ConfigTest(unittest.TestCase):
    def test_config_synced(self):
        src = open("telegram_monitor/config.py", encoding="utf-8").read()
        self.assertIn("REPLY_ACK_DEDUP", src)
        self.assertIn("reply_ack_dedup_enabled", src)
        env = open(".env.example", encoding="utf-8").read()
        self.assertRegex(env, re.compile(r"^REPLY_ACK_DEDUP=1", re.M))
        self.assertIn("REPLY_ACK_DEDUP", open("README.md", encoding="utf-8").read())

    def test_getattr_defaults_false(self):
        self.assertFalse(getattr(SimpleNamespace(), "reply_ack_dedup_enabled", False))


if __name__ == "__main__":
    unittest.main()
