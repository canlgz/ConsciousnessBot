"""🤝 §1.55 跨句補時距（PROMISE_ANCHOR_BRIDGE）：時距在上一句、動作在這一句＝拼起來才是一條約定。

截圖根因（11:05–11:06，使用者原話「bot 看不懂使用者的對話表達方式的真正語意及意圖」）：
「給你思考20分鐘。」＋「時間到了再跟我說，你可以如何證明自己？」拆成兩輪，各自都是碎片（實測）：
- m1：capture／§0.61 守門／temporal **全 miss**（裸時距無動作）→ LLM 空口答應「我記下來了，我會等 20 分鐘」；
- m2：有動作無鐘點 → capture miss、守門有掛但 LLM 沒守住 → 又空口答應＋把「時間到了『再』跟我說」的
  「時間到了」讀成**現時宣稱** → 回「嗯？現在才 11:05 呢。」（條件式被當成宣稱＝語意誤讀的直接來源）；
- **joined（兩句拼起來）：sched=True、temporal 解出正確 epoch**＝一條完美的排程承諾。

§1.55 橋接（全確定性、零 LLM、時刻永遠 temporal）：本輪 capture miss ∧ 這句是請託形且自身無鐘點 ∧
上一則使用者訊息 180 秒內且**自身也是懸空碎片**（capture/守門皆 miss＝不會重收「10分鐘後提醒我」）∧
拼句 capture 命中＋解得出未來時刻 → 走既有 _book_scheduled_targets 真入帳（ack 帶程式算 HH:MM、到點真兌現）。
旗標兩層分離：config 預設 True／monitor 端 getattr 預設 False＝不橋＝逐位元同現狀。全 stub、零網路。
"""

import os
import re
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from zoneinfo import ZoneInfo

from telegram_monitor import monitor, selfstate, temporal
from telegram_monitor.state import State

TZ = ZoneInfo("Asia/Taipei")
NOW = datetime(2026, 7, 19, 3, 5, 0, tzinfo=timezone.utc)   # 台北 11:05（截圖時刻）

M1 = "給你思考20分鐘。"
M2 = "時間到了再跟我說，你可以如何證明自己？"


# ── 碎片語意釘住（截圖根因的實測結論固化）──────────────────────────────────
class FragmentPinTest(unittest.TestCase):
    def test_fragments_miss_alone_join_hits(self):
        self.assertFalse(selfstate.is_scheduled_promise_request(M1))     # 裸時距無動作＝碎片
        self.assertFalse(selfstate.looks_like_timed_request(M1, NOW, TZ))
        self.assertFalse(selfstate.is_scheduled_promise_request(M2))     # 有動作無鐘點＝碎片
        self.assertTrue(selfstate.looks_like_timed_request(M2, NOW, TZ))  # 守門認得請託形
        self.assertEqual(temporal.all_clock_epochs(M2, NOW, TZ), [])
        joined = M1 + "\n" + M2
        self.assertTrue(selfstate.is_scheduled_promise_request(joined))   # 拼起來＝完整約定
        targets = temporal.all_clock_epochs(joined, NOW, TZ)
        self.assertEqual(len(targets), 1)
        self.assertAlmostEqual(targets[0], NOW.timestamp() + 20 * 60, delta=90)


# ── 整合：兩輪對話 → 第二輪橋接入帳 ────────────────────────────────────────
class BridgeIntegrationTest(unittest.TestCase):
    class Cl:
        def __init__(self): self.sent, self.dry_run = [], False
        def send(self, t): self.sent.append(t); return True

    def _cfg(self, bridge=True):
        base = dict(dry_run=False, telegram_chat_id="", scheduled_promise_enabled=True,
                    promise_emit_enabled=True, promise_sched_ttl_sec=21600, timezone="Asia/Taipei",
                    notify_cooldown_min=30, promise_reply_bridge_enabled=False, promise_ledger_enabled=True,
                    sched_leave_autoarm_enabled=True, deferred_promise_enabled=False,
                    promise_llm_rescue_enabled=False, sticker_llm_rescue_enabled=False,
                    promise_keep_claim_guard_enabled=True, promise_said_ground_enabled=True,
                    bot_self_promise_enabled=False, promise_preempt_enabled=False,
                    sticker_sent_memory_enabled=True, send_stickers=True,
                    sticker_fakesend_guard_enabled=False, recall_ground_guard_enabled=False,
                    selfshare_reason_ground_enabled=False, wake_projection_guard_enabled=False,
                    user_habit_ground_enabled=False, self_feel_condense_enabled=False,
                    promise_deliver_content_enabled=False, mood_coord_report_enabled=False)
        if bridge is not None:
            base["promise_anchor_bridge_enabled"] = bridge
        return SimpleNamespace(**base)

    def _coach(self):
        c = SimpleNamespace(enabled=True, api_key="k", model="m",
                            meter=SimpleNamespace(record=lambda *a, **k: None), acks=[])
        c.ask = lambda *a, **k: ("chat", None, "嗯。")
        c.reply = lambda *a, **k: "嗯。"
        c.voice_schedule_ack = lambda q, w, h, sticker_hint="": (c.acks.append(w) or f"好，{w.strftime('%H:%M')}我會來。")
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

    def _state(self):
        s = State(os.path.join(tempfile.mkdtemp(), "s.json"))
        s.owner_folder_id = "F"
        return s

    def _turn(self, s, text, cfg, when):
        cl, co = self.Cl(), self._coach()
        monitor.handle_message({"message": {"chat": {"id": 1}, "text": text, "date": when.timestamp()}},
                               co, self.BOOM, {"meta": {}, "records": []}, self.SNAP,
                               s, cl, cfg, TZ)
        return cl, co

    def test_screenshot_pair_books_once(self):
        s, cfg = self._state(), self._cfg()
        self._turn(s, M1, cfg, NOW)
        self.assertFalse(getattr(s, "scheduled_promises", None) or [])   # m1 單獨＝不入帳（碎片）
        cl2, co2 = self._turn(s, M2, cfg, NOW + timedelta(seconds=30))
        proms = getattr(s, "scheduled_promises", None) or []
        self.assertEqual(len(proms), 1)                                  # 拼句橋接＝入帳一筆
        self.assertAlmostEqual(proms[0]["target_ts"],
                               (NOW + timedelta(seconds=30)).timestamp() + 20 * 60, delta=120)
        self.assertTrue(co2.acks)                                        # ack 帶程式算的 HH:MM（真入帳、非空口）

    def test_flag_off_bitwise_no_booking(self):
        # 【消融】旗標關/缺席＝同現狀：兩句都空手（LLM 空口答應的截圖行為保留給 LLM 層）
        for bridge in (False, None):
            s, cfg = self._state(), self._cfg(bridge=bridge)
            self._turn(s, M1, cfg, NOW)
            self._turn(s, M2, cfg, NOW + timedelta(seconds=30))
            self.assertFalse(getattr(s, "scheduled_promises", None) or [], str(bridge))

    def test_prev_already_booked_not_rebooked(self):
        # 上一句自己就是完整約定（10分鐘後提醒我＝已入帳）→ 這句不橋（不重收、不雙帳）
        s, cfg = self._state(), self._cfg()
        self._turn(s, "10分鐘後提醒我", cfg, NOW)
        self.assertEqual(len(getattr(s, "scheduled_promises", None) or []), 1)
        self._turn(s, M2, cfg, NOW + timedelta(seconds=30))
        self.assertEqual(len(getattr(s, "scheduled_promises", None) or []), 1)

    def test_window_expired_no_bridge(self):
        # 上一句超過 180 秒＝過期碎片 → 不橋（十分鐘前隨口一句 20 分鐘不該被翻出來）
        s, cfg = self._state(), self._cfg()
        self._turn(s, M1, cfg, NOW)
        self._turn(s, M2, cfg, NOW + timedelta(minutes=10))
        self.assertFalse(getattr(s, "scheduled_promises", None) or [])


# ── 同步 ───────────────────────────────────────────────────────────────────
class ConfigTest(unittest.TestCase):
    def test_config_synced(self):
        src = open("telegram_monitor/config.py", encoding="utf-8").read()
        self.assertIn("PROMISE_ANCHOR_BRIDGE", src)
        self.assertIn("promise_anchor_bridge_enabled", src)
        env = open(".env.example", encoding="utf-8").read()
        self.assertRegex(env, re.compile(r"^PROMISE_ANCHOR_BRIDGE=1", re.M))
        self.assertIn("PROMISE_ANCHOR_BRIDGE", open("README.md", encoding="utf-8").read())

    def test_getattr_defaults_false(self):
        self.assertFalse(getattr(SimpleNamespace(), "promise_anchor_bridge_enabled", False))


if __name__ == "__main__":
    unittest.main()
