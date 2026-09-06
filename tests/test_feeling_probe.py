"""🗜️ §1.51 情感探問要真回答（FEELING_PROBE_DEPTH）：適性篇幅＋先答後問。

截圖根因（09:54–09:55）：使用者問「羨慕我嗎」→ bot 回「嗯...」「羨慕嗎？」＝支吾＋把問題丟回去。
機制鏈：fact_or_chat base=1 ＋ 短輸入鏡射（≤6 字 −1）＋（低落心情再 −1）→ verbosity level 0、泡泡 ≤2
＝LLM 拿到「壓到底」的篇幅預算，只好填兩顆停頓泡泡。**長度啟發式把「輸入長短」誤當「意圖深淺」**——
「羨慕我嗎」句短但在探 bot 對他的心意，值得真的回答（使用者原話：要了解對話意圖、適性調整長度與深度）。

§1.51 三件（一旗；config 預設 True／monitor 端 getattr 預設 False＝逐位元同現狀）：
A. selfstate.is_feeling_probe——對 bot 心意/感受的**短問句**（情感詞×問句形×人稱指向；內容問句
   （什麼/哪/怎麼…）排除；表小而封閉、漏了＝現狀短答＝安全側）；
B. verbosity.assess 新尾參 probe——命中＝level 地板 2（別被短輸入鏡射/低落心情壓成支吾）；
   使用者明示要短（簡單說）與 §1.14 敵意收斂**仍優先**（早退不受地板影響）；
C. persona.FEELING_PROBE_HINT——先正面回答（最接近的真實狀態）、一兩句為什麼（照此刻內在座標與
   對他的印象）、反問只能放在答後；機制沒有的情緒誠實說最接近的真話（不演不假裝）。
   另：命中探問＝真問 bot 自己 → §1.43 感覺修剪不 arm（真問心意時長答合理）。全 stub、零網路。
"""

import os
import re
import tempfile
import unittest
from datetime import datetime, timezone
from types import SimpleNamespace
from zoneinfo import ZoneInfo

from telegram_monitor import monitor, persona, selfstate, verbosity
from telegram_monitor.state import State

TZ = ZoneInfo("Asia/Taipei")
NOW = datetime(2026, 7, 19, 1, 54, 0, tzinfo=timezone.utc)   # 台北 09:54（截圖時刻）


# ── 單元：A 情感探問偵測（純函式）──────────────────────────────────────────
class ProbeDetectorTest(unittest.TestCase):
    def test_probes_hit(self):
        for t in ("羨慕我嗎", "你喜歡我嗎", "想我嗎", "你在乎我嗎？", "你會想我嗎",
                  "你開心嗎", "你難過嗎", "你討厭我嗎", "愛我嗎", "你嫉妒我吧"):
            self.assertTrue(selfstate.is_feeling_probe(t), t)

    def test_non_probes_false(self):
        for t in ("我好難過",                      # 自述，不是問 bot
                  "你開心就好",                    # 無問句形
                  "羨慕死了",                      # 無問句形
                  "你喜歡吃什麼嗎",                # 內容問句（什麼）＝要資訊不是要立場
                  "你怎麼會羨慕我嗎",              # 內容問句（怎麼）
                  "今天天氣好嗎", "早安", "說啊", "", None):
            self.assertFalse(selfstate.is_feeling_probe(t), repr(t))

    def test_long_sentences_excluded(self):
        # 長句自帶篇幅訊號（≥40 字 +1／deep-cue +1），不需要探問地板 → 只收短問
        self.assertFalse(selfstate.is_feeling_probe("我一直在想你到底會不會羨慕我這樣的生活呢還是說其實不會"))


# ── 單元：B verbosity 地板 ──────────────────────────────────────────────────
class VerbosityProbeTest(unittest.TestCase):
    class _S:
        self_asks = {}

    def test_probe_floor_level2(self):
        # 截圖 case：HEAD＝(0,2)（釘住 bug）；probe=True＝地板 2（句短但意圖深）
        self.assertEqual(verbosity.assess("羨慕我嗎", "fact_or_chat", 0.0, self._S()),
                         verbosity.Scale(0, 2))
        s = verbosity.assess("羨慕我嗎", "fact_or_chat", 0.0, self._S(), probe=True)
        self.assertGreaterEqual(s.level, 2)

    def test_probe_floor_beats_low_mood(self):
        # 低落心情（−1）也壓不破地板——支吾反問不會因為「心情低」就變合理
        s = verbosity.assess("羨慕我嗎", "fact_or_chat", -0.5, self._S(), probe=True)
        self.assertGreaterEqual(s.level, 2)

    def test_brief_cue_still_wins(self):
        # 使用者明示要短（簡單說）→ 仍壓到底（明示 > 地板）
        self.assertEqual(verbosity.assess("簡單說你羨慕我嗎", "fact_or_chat", 0.0, self._S(), probe=True),
                         verbosity.Scale(0, 2))

    def test_hostile_still_wins(self):
        # §1.14 氣頭收斂 > 地板（氣頭上不長篇）
        self.assertEqual(verbosity.assess("羨慕我嗎", "fact_or_chat", 0.0, self._S(),
                                          hostile=True, probe=True),
                         verbosity.Scale(0, 2))

    def test_kwarg_absent_bitwise_head(self):
        for t, kind in (("羨慕我嗎", "fact_or_chat"), ("嗨", "smalltalk"), ("你是有意識的嗎", "self_consciousness")):
            self.assertEqual(verbosity.assess(t, kind, 0.0, self._S()),
                             verbosity.assess(t, kind, 0.0, self._S(), probe=False), t)


# ── 整合：hint 注入＋篇幅傳遞＋§1.43 不修剪 ────────────────────────────────
class IntegrationTest(unittest.TestCase):
    class Cl:
        def __init__(self): self.sent, self.dry_run = [], False
        def send(self, t): self.sent.append(t); return True

    def _cfg(self, probe=True, condense=False):
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
                    user_habit_ground_enabled=False, self_feel_condense_enabled=condense,
                    promise_deliver_content_enabled=False, mood_coord_report_enabled=False)
        if probe is not None:
            base["feeling_probe_depth_enabled"] = probe
        return SimpleNamespace(**base)

    def _coach(self):
        seen = {"ask": None, "reply": None}
        c = SimpleNamespace(enabled=True, api_key="k", model="m",
                            meter=SimpleNamespace(record=lambda *a, **k: None), seen=seen)

        def ask(*a, **k):
            seen["ask"] = k.get("extra_system")
            return ("chat", None, "嗯。")

        def reply(*a, **k):
            seen["reply"] = k.get("extra_system")
            return "嗯。"

        c.ask = ask
        c.reply = reply
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

    def _run(self, text, cfg):
        s = State(os.path.join(tempfile.mkdtemp(), "s.json"))
        s.owner_folder_id = "F"
        s.entropy = SimpleNamespace(mood=0.0, arousal=0.0, hunger=0.5, self_stims_this_idle=0,
                                    reach_outs_this_idle=0, coping_reach_outs_this_idle=0)
        co = self._coach()
        monitor.handle_message({"message": {"chat": {"id": 1}, "text": text, "date": NOW.timestamp()}},
                               co, self.BOOM, {"meta": {}, "records": []}, self.SNAP,
                               s, self.Cl(), cfg, TZ)
        return co

    def test_probe_gets_depth_and_hint(self):
        co = self._run("羨慕我嗎", self._cfg())
        self.assertGreaterEqual(co._turn_length, 2)          # 篇幅地板（不再 level 0 支吾）
        got = (co.seen["ask"] or "") + (co.seen["reply"] or "")
        self.assertIn("心意", got)                            # FEELING_PROBE_HINT 已注入（先答後問）

    def test_flag_off_bitwise_head(self):
        # 【消融】旗標關/缺席＝同現狀：level 0（截圖 bug 保留）、無 hint
        for probe in (False, None):
            co = self._run("羨慕我嗎", self._cfg(probe=probe))
            self.assertEqual(co._turn_length, 0, str(probe))
            got = (co.seen["ask"] or "") + (co.seen["reply"] or "")
            self.assertNotIn("心意", got, str(probe))

    def test_non_probe_unaffected(self):
        co = self._run("今天天氣不錯", self._cfg())
        got = (co.seen["ask"] or "") + (co.seen["reply"] or "")
        self.assertNotIn("心意", got)

    def test_condense_trim_not_armed_for_probe(self):
        # §1.43 修剪：探問心意＝真問 bot 自己 → 不 arm（長答合理）；旗標關＝照舊 arm（HEAD）
        self._run("羨慕我嗎", self._cfg(probe=True, condense=True))
        self.assertFalse(monitor._TURN.get("self_feel_trim"))
        self._run("羨慕我嗎", self._cfg(probe=False, condense=True))
        self.assertTrue(monitor._TURN.get("self_feel_trim"))


# ── 同步 ───────────────────────────────────────────────────────────────────
class ConfigTest(unittest.TestCase):
    def test_config_synced(self):
        src = open("telegram_monitor/config.py", encoding="utf-8").read()
        self.assertIn("FEELING_PROBE_DEPTH", src)
        self.assertIn("feeling_probe_depth_enabled", src)
        env = open(".env.example", encoding="utf-8").read()
        self.assertRegex(env, re.compile(r"^FEELING_PROBE_DEPTH=1", re.M))
        self.assertIn("FEELING_PROBE_DEPTH", open("README.md", encoding="utf-8").read())

    def test_persona_hint_exists(self):
        self.assertTrue(hasattr(persona, "FEELING_PROBE_HINT"))
        self.assertIn("先", persona.FEELING_PROBE_HINT)
        self.assertIn("心意", persona.FEELING_PROBE_HINT)

    def test_getattr_defaults_false(self):
        self.assertFalse(getattr(SimpleNamespace(), "feeling_probe_depth_enabled", False))


if __name__ == "__main__":
    unittest.main()
