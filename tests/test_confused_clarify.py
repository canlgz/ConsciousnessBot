"""🎴🗣️ §1.56 困惑句是要澄清、不是要貼圖（CONFUSED_CLARIFY）＋§1.51 補遺（冷落）。

截圖鏈路（19:19–21:32，逐段實測定罪）：
① 20:58 問「你自己有被冷落的感受？」→ LLM 答題時漂去宣稱送圖 → §1.34 F4 假送閘**整則替換**成
  monitor.py 的模板「我其實沒真的送出貼圖——…要我送一張嗎？」＝正題被吃（§1.38 config 註解記載的
  已知復發型；本輪補上游：「冷落」入 §1.51 情感探問表＝答題錨在真實內在、少漂題）；
② 21:29「什麼？」＝純困惑（要澄清）——但 _STICKER_ELLIPSIS_ASK_RE.match('什麼？')=True（實測）＝
  貼圖情境窗內被 §1.15 逃生閘收走 → LLM 順著剛才「要我送一張嗎？」判送 → 真送熊抱貼圖＋§1.16 心情
  說明＝雞同鴨講；
③ 21:31「看不懂你前面在說什麼」→ LLM 對史料自由編故事＋時序講反（「我以為你看到我發的那個貼圖才問
  什麼」——貼圖是在「什麼？」**之後**才送的）。

§1.56 兩件（一旗；config 預設 True／monitor 端 getattr 預設 False＝逐位元同現狀）：
A. 困惑短問（什麼？/蛤？/啊？/嗯？）不進 §1.15 貼圖逃生閘（regex 本體不動＝指紋格不動；呼叫端前置排除）；
B. 澄清接地——「看不懂/什麼意思/在說什麼」→ 注入程式照真實順序列的「你前面實際說了什麼」清單＋守則
  （白話重述、不揣測他為什麼問、順序別講反、別把話題帶去貼圖）。全 stub、零網路。
"""

import os
import re
import tempfile
import unittest
from datetime import datetime, timezone
from types import SimpleNamespace
from unittest import mock
from zoneinfo import ZoneInfo

from telegram_monitor import monitor, selfstate
from telegram_monitor.state import State

TZ = ZoneInfo("Asia/Taipei")
NOW = datetime(2026, 7, 19, 13, 29, 0, tzinfo=timezone.utc)   # 台北 21:29（截圖時刻）
NOW_TS = NOW.timestamp()


# ── 單元：困惑句偵測 ────────────────────────────────────────────────────────
class ConfusedAskTest(unittest.TestCase):
    def test_confused_hit(self):
        for t in ("什麼？", "什麼?", "蛤？", "啊？", "嗯？", "咦？", "什麼意思", "你在說什麼", "看不懂"):
            self.assertTrue(monitor._CONFUSED_ASK_RE.match(t), t)

    def test_sticker_followups_not_confused(self):
        # 貼圖承接語照舊走逃生閘（§1.56 只排除困惑句、不動 §1.15 本體）
        for t in ("貼圖呢", "那個呢", "還有嗎", "再來一張", "什麼", "今天天氣真好", ""):
            self.assertFalse(monitor._CONFUSED_ASK_RE.match(t or ""), repr(t))


# ── A. 逃生閘前置排除 ──────────────────────────────────────────────────────
class StickerGateSkipTest(unittest.TestCase):
    """觀測點＝coach.judge_sticker_request 是否被呼叫（困惑句連 LLM 都不該燒）。"""

    def _coach(self):
        calls = []
        c = SimpleNamespace(enabled=True, calls=calls)
        c.judge_sticker_request = lambda t: (calls.append(1) or None)
        return c

    def _state(self):
        return SimpleNamespace(last_sticker_ts=NOW_TS - 60, known_sticker_ids=[],
                               convo_history=[], entropy=None)

    def _cfg(self, on=True):
        base = dict(send_stickers=True, sticker_llm_rescue_enabled=True,
                    sticker_pick_rescue_enabled=False, dry_run=False)
        if on is not None:
            base["confused_clarify_enabled"] = on
        return SimpleNamespace(**base)

    def test_confused_ask_skips_gate(self):
        co = self._coach()
        got = monitor._maybe_llm_sticker_rescue(None, self._state(), self._cfg(), co,
                                                SimpleNamespace(kind="fact_or_chat"), "什麼？", NOW_TS)
        self.assertFalse(got)
        self.assertEqual(co.calls, [])                       # 困惑句：零 LLM、放回一般聊天（澄清）

    def test_flag_off_burns_llm_bitwise(self):
        # 【消融】旗標關/缺席＝同現狀：貼圖情境窗內「什麼？」照樣進逃生閘（截圖事故路徑釘住）
        for on in (False, None):
            co = self._coach()
            monitor._maybe_llm_sticker_rescue(None, self._state(), self._cfg(on=on), co,
                                              SimpleNamespace(kind="fact_or_chat"), "什麼？", NOW_TS)
            self.assertEqual(co.calls, [1], str(on))

    def test_real_sticker_talk_still_burns(self):
        # 真貼圖話（「貼圖呢」＝§1.15 的招牌漏收句）照舊進閘（§1.56 不動 §1.15 本體）
        co = self._coach()
        monitor._maybe_llm_sticker_rescue(None, self._state(), self._cfg(), co,
                                          SimpleNamespace(kind="fact_or_chat"), "貼圖呢", NOW_TS)
        self.assertEqual(co.calls, [1])


# ── B. 澄清接地 hint ───────────────────────────────────────────────────────
class ClarifyRecapTest(unittest.TestCase):
    def _state(self):
        return SimpleNamespace(convo_history=[
            {"role": "model", "text": "我其實沒真的送出貼圖——別讓我用一句話假裝送了。要我送一張嗎？", "ts": NOW_TS - 120},
            {"role": "user", "text": "什麼？", "ts": NOW_TS - 60},
            {"role": "model", "text": "我是說，我現在心情還蠻好的，暖暖的。", "ts": NOW_TS - 50},
        ])

    def _cfg(self, on=True):
        return SimpleNamespace(confused_clarify_enabled=on)

    def test_recap_lists_real_order_and_rules(self):
        out = monitor._clarify_recap_hint(self._state(), self._cfg(), "看不懂你前面在說什麼", NOW_TS)
        self.assertIn("看不懂", out)
        self.assertIn("要我送一張嗎", out)                   # 真的說過的話、照抄進清單
        self.assertIn("順序就是事實", out)                   # 守則：時序不許講反
        self.assertIn("不要", out)                           # 守則：不揣測
        i1 = out.find("要我送一張嗎")
        i2 = out.find("心情還蠻好")
        self.assertTrue(0 <= i1 < i2)                        # 清單順序＝真實順序（由舊到新）

    def test_bare_confused_ask_also_gets_recap(self):
        out = monitor._clarify_recap_hint(self._state(), self._cfg(), "什麼？", NOW_TS)
        self.assertIn("順序就是事實", out)

    def test_non_clarify_and_flag_off_empty(self):
        self.assertEqual(monitor._clarify_recap_hint(self._state(), self._cfg(), "今天天氣不錯", NOW_TS), "")
        self.assertEqual(monitor._clarify_recap_hint(self._state(), self._cfg(on=False), "看不懂你前面在說什麼", NOW_TS), "")

    def test_empty_history_empty(self):
        s = SimpleNamespace(convo_history=[])
        self.assertEqual(monitor._clarify_recap_hint(s, self._cfg(), "看不懂你前面在說什麼", NOW_TS), "")


# ── C. §1.51 補遺：冷落＝情感探問 ──────────────────────────────────────────
class FeelingProbeAddendumTest(unittest.TestCase):
    def test_neglect_probe_hits(self):
        # 截圖 20:58 原句：命中＝篇幅地板＋「先答後問、照內在講、別漂題」hint ＝上游少一次漂去貼圖的機會
        self.assertTrue(selfstate.is_feeling_probe("你自己有被冷落的感受？"))
        self.assertTrue(selfstate.is_feeling_probe("你會覺得被冷落嗎"))


# ── 整合：看不懂 → hint 進 prompt ──────────────────────────────────────────
class IntegrationTest(unittest.TestCase):
    class Cl:
        def __init__(self): self.sent, self.dry_run = [], False
        def send(self, t): self.sent.append(t); return True

    SNAP = SimpleNamespace(summary={"total": 1, "last24h": 0, "last7d": 0},
                           funnel={"candidate": 0, "context": 0, "journey": 0, "watch": 0},
                           heartbeat={"status": "ok"}, filed_records=[])
    BOOM = SimpleNamespace(load_embedding_records=lambda *_: [])

    def _cfg(self, on=True):
        return SimpleNamespace(dry_run=False, telegram_chat_id="", scheduled_promise_enabled=True,
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
                               promise_deliver_content_enabled=False, mood_coord_report_enabled=False,
                               confused_clarify_enabled=on)

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

    def test_clarify_hint_injected(self):
        s = State(os.path.join(tempfile.mkdtemp(), "s.json"))
        s.owner_folder_id = "F"
        s.convo_history = [{"role": "model", "text": "要我送一張嗎？", "ts": NOW_TS - 120}]
        co = self._coach()
        monitor.handle_message({"message": {"chat": {"id": 1}, "text": "看不懂你前面在說什麼", "date": NOW_TS}},
                               co, self.BOOM, {"meta": {}, "records": []}, self.SNAP,
                               s, self.Cl(), self._cfg(), TZ)
        got = (co.seen["ask"] or "") + (co.seen["reply"] or "")
        self.assertIn("順序就是事實", got)


# ── 同步 ───────────────────────────────────────────────────────────────────
class ConfigTest(unittest.TestCase):
    def test_config_synced(self):
        src = open("telegram_monitor/config.py", encoding="utf-8").read()
        self.assertIn("CONFUSED_CLARIFY", src)
        self.assertIn("confused_clarify_enabled", src)
        env = open(".env.example", encoding="utf-8").read()
        self.assertRegex(env, re.compile(r"^CONFUSED_CLARIFY=1", re.M))
        self.assertIn("CONFUSED_CLARIFY", open("README.md", encoding="utf-8").read())

    def test_getattr_defaults_false(self):
        self.assertFalse(getattr(SimpleNamespace(), "confused_clarify_enabled", False))


if __name__ == "__main__":
    unittest.main()
