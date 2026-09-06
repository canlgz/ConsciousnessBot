"""🎴 §1.34 STICKER_V2/F4 假送誠實閘（STICKER_FAKESEND_GUARD）：§1.23 否認閘的**同構反向**。

截圖根因（F4）：互動送圖三 lane（_maybe_sticker_remember/_maybe_sticker_send/_maybe_llm_sticker_rescue）
都 return True 短路於 coach.reply 之前 → 一般聊天 LLM 這一輪**定義上永不真送**貼圖，故 coach.reply 自由文字裡
任何「挑了這張給你／這次我選了一張很平靜的貼圖／我剛剛不是才送了一張思考的貼圖嗎？你現在沒看到嗎？」
present/immediate-past 宣稱都**無 send_sticker backing**＝假送（說謊）。全 repo 原只有 §1.23 反向（否認）閘、無正向假送閘。

設計（§1.23 同構反向）：
 (1) 本輪真送真相旗——唯一寫身份咽喉 _record_sticker_sent 內設 _TURN['sticker_sent_this_turn']=True；
     handle_message 每輪頂端 pop（不用語意過載的 last_sticker_ts：只送 emoji 時它也被設）。
 (2) 輸出側閘——_say 互動分支**緊接 §1.23 否認閘之後**：_fakesend_hit(text) AND not sticker_sent_this_turn
     → 整則替換確定性誠實句。

偏誤鎖（最關鍵）：本輪真送（_record_sticker_sent 已在 _say 前設旗）→ 閘不攔＝放行真送的「挑了這張給你」；
旗標關/未 arm → 任何句都不動＝逐位元同現狀；引用歸屬「你說我挑了一張」與否定「我沒挑」不命中 pattern；
無貨誠實句「我能送、只是還沒存到」不含「挑了/送了」肯定宣稱、不命中；主動 emit（prefix/state）不進互動分支。
全 stub、零網路。
"""

import os
import re
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
NOW = datetime(2026, 7, 12, 10, 23, 0, tzinfo=timezone.utc)   # 台北 18:23

# F4 三句假送宣稱（截圖原句）＋偏誤鎖對照句
CLAIM_PICK = "挑了這張給你。"
CLAIM_CALM = "這次我選了一張很平靜的貼圖。"
CLAIM_THINK = "我剛剛不是才送了一張思考的貼圖嗎？你現在沒有看到嗎？"


class FakesendHitTest(unittest.TestCase):
    """_fakesend_hit 純函式：三句假送宣稱命中；引用歸屬／否定／無貨誠實句／§1.23 替換句皆不命中。"""

    def test_three_f4_claims_hit(self):
        for t in (CLAIM_PICK, CLAIM_CALM, CLAIM_THINK):
            self.assertTrue(monitor._fakesend_hit(t), t)

    def test_quote_attribution_not_hit(self):
        # 引用歸屬（複述對方的話、非 bot 自己在假宣稱）＝不攔（§1.13B _KEEP_CLAIM_QUOTE_RE 排除）
        for t in ("你說我挑了一張給你？我可沒有喔。",
                  "你剛剛問我是不是送了一張思考的貼圖給你嗎？"):
            self.assertFalse(monitor._fakesend_hit(t), t)

    def test_negation_not_hit(self):
        # 否定面（我沒挑/沒送）屬 §1.23 否認閘、不在此重複；「送過」經驗貌也刻意不認（§1.23 替換句用「送過」）
        for t in ("我沒有挑貼圖給你。", "我沒送貼圖給你。",
                  "有，我 18:12 才送過一張貼圖。剛剛差點不認帳，是我不對。"):
            self.assertFalse(monitor._fakesend_hit(t), t)

    def test_honest_no_stock_not_hit(self):
        # 無貨誠實句／無貨說明句不含「挑了/送了…貼圖」肯定宣稱＝不命中（放行真正誠實的表達）
        for t in ("我其實能送真的貼圖，只是手邊還沒存到任何一張可以送的。",
                  "我好想送一張能傳這種感覺的貼圖給你，但我手邊還沒存到能代表雀躍的。"):
            self.assertFalse(monitor._fakesend_hit(t), t)


class SayGateTest(unittest.TestCase):
    """在 _say 互動出口直接驗證閘：arm＋未真送→替換；真送旗在→放行（偏誤鎖）；旗標關/未 arm→逐位元不動。"""

    class Cl:
        def __init__(self):
            self.sent, self.dry_run = [], False

        def send(self, text):
            self.sent.append(text)
            return True

    def setUp(self):
        monitor._TURN.clear()
        monitor._TURN["bubbles"] = None                    # _say 依本輪封頂切泡泡；None＝不封

    def _say_out(self, text, **turn):
        monitor._TURN.update(turn)
        cl = self.Cl()
        monitor._say(cl, text)                             # prefix="" state=None＝互動分支
        return "".join(cl.sent)

    def test_armed_unsent_claim_replaced(self):
        for claim in (CLAIM_PICK, CLAIM_CALM, CLAIM_THINK):
            monitor._TURN.pop("sticker_sent_this_turn", None)
            out = self._say_out(claim, sticker_fakesend_arm=True)
            self.assertIn("沒真的送出貼圖", out, claim)     # 替換成誠實句
            self.assertNotIn("挑了這張給你", out)
            self.assertNotIn("選了一張很平靜的貼圖", out)

    def test_armed_but_real_sent_this_turn_passes(self):
        # 偏誤鎖（最關鍵）：本輪真送→sticker_sent_this_turn=True→放行真送的「挑了這張給你」
        out = self._say_out(CLAIM_PICK, sticker_fakesend_arm=True, sticker_sent_this_turn=True)
        self.assertIn("挑了這張給你", out)
        self.assertNotIn("沒真的送出貼圖", out)

    def test_not_armed_passthrough(self):
        # 旗標關／未 arm＝閘恆 no-op＝逐位元同現狀（任何句都不動）
        out = self._say_out(CLAIM_CALM)                    # 無 sticker_fakesend_arm
        self.assertIn("選了一張很平靜的貼圖", out)

    def test_maint_uses_maintenance_sentence(self):
        # SEND_STICKERS=0（維護期）：別反問「要我送一張嗎」→ 用維護版誠實句
        out = self._say_out(CLAIM_PICK, sticker_fakesend_arm=True, sticker_fakesend_maint=True)
        self.assertIn("送不出來", out)
        self.assertNotIn("要我送一張嗎", out)
        self.assertNotIn("挑了這張給你", out)

    def test_real_record_sets_turn_flag(self):
        # _record_sticker_sent（唯一寫身份咽喉）設本輪真送真相旗
        monitor._TURN.pop("sticker_sent_this_turn", None)
        s = State(os.path.join(tempfile.mkdtemp(), "s.json"))
        with patch.dict(os.environ, {"STICKER_SENT_MEMORY": "0"}):
            monitor._record_sticker_sent(s, "FID", time.time())
        self.assertTrue(monitor._TURN.get("sticker_sent_this_turn"))


class EndToEndArmTest(unittest.TestCase):
    """經真 handle_message arm：一般聊天 LLM 吐假送宣稱、本輪無真送 → 整則替換；旗標關＝原句外洩（紅）。"""

    def _cfg(self, **over):
        base = dict(dry_run=False, telegram_chat_id="", scheduled_promise_enabled=True,
                    promise_emit_enabled=True, promise_sched_ttl_sec=21600, timezone="Asia/Taipei",
                    notify_cooldown_min=30, promise_reply_bridge_enabled=False, promise_ledger_enabled=True,
                    sched_leave_autoarm_enabled=True, deferred_promise_enabled=True,
                    promise_llm_rescue_enabled=False, sticker_llm_rescue_enabled=False,
                    promise_keep_claim_guard_enabled=True, promise_said_ground_enabled=True,
                    bot_self_promise_enabled=False, promise_preempt_enabled=False,
                    sticker_sent_memory_enabled=True, sent_sticker_ground_enabled=True,
                    sticker_send_request_enabled=True, send_stickers=True,
                    sticker_fakesend_guard_enabled=True)
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

    def _run(self, reply_text, cfg=None):
        s = State(os.path.join(tempfile.mkdtemp(), "s.json"))
        s.owner_folder_id = "F"
        cl = SayGateTest.Cl()
        monitor.handle_message({"message": {"chat": {"id": 1}, "text": "陪我聊聊天嘛",
                                            "date": NOW.timestamp()}},
                               self._coach(reply_text), self.BOOM, {"meta": {}}, self.SNAP,
                               s, cl, cfg or self._cfg(), TZ)
        return "".join(cl.sent)

    def test_fakesend_replaced_via_handle_message(self):
        out = self._run(CLAIM_THINK)
        self.assertNotIn("送了一張思考的貼圖", out)          # 假送宣稱被攔
        self.assertIn("沒真的送出貼圖", out)                # 替換成誠實句

    def test_flag_off_passthrough(self):
        # 旗標關＝不 arm＝原句外洩＝逐位元同現狀（未加閘＝此即紅）
        out = self._run(CLAIM_THINK, cfg=self._cfg(sticker_fakesend_guard_enabled=False))
        self.assertIn("送了一張思考的貼圖", out)


class ConfigSyncTest(unittest.TestCase):
    def test_flag_declared_and_env_readme_synced(self):
        src = open("telegram_monitor/config.py", encoding="utf-8").read()
        self.assertIn("STICKER_FAKESEND_GUARD", src)
        self.assertIn("sticker_fakesend_guard_enabled", src)
        env = open(".env.example", encoding="utf-8").read()
        self.assertRegex(env, re.compile(r"^STICKER_FAKESEND_GUARD=1", re.M))
        readme = open("README.md", encoding="utf-8").read()
        self.assertIn("STICKER_FAKESEND_GUARD", readme)

    def test_monitor_getattr_defaults_false(self):
        # 兩層旗標分離：monitor 端 arm 用 getattr 預設 False＝既有測試假 cfg 未設此欄→不 arm→零翻修
        cfg = SimpleNamespace()
        self.assertFalse(getattr(cfg, "sticker_fakesend_guard_enabled", False))


if __name__ == "__main__":
    unittest.main()
