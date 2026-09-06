"""🧭 §1.46 敵意不推暖（HOSTILE_AFFECT_FIX）：被罵時 entropy.mood 不再反而上升。

根因（§1.14 落地時註記「敵意文字推暖的汙染是另案」、§1.45 repro case 1 實證）：
reaction.affect_delta_for 只認 _CHALLENGE 詞表；「你太爛了」（at-bot regex）「我不信」（全句短句錨）這類
is_hostile=True 的句子落到「一般陪伴」預設分支回 (+0.05, +0.06) → 被罵 mood 反而變暖，§1.45 軌跡同輪寫著
「被說了重話」＝數字與說法自相矛盾。

修法：reaction.hostile_affect_delta_for 純函式——is_hostile 命中**且**落「一般陪伴」預設的句子改回質疑方向
（V−A+，與 _CHALLENGE 同格）；詞表已分類（暖意/質疑/低落）原值原樣。monitor.py 互動輪 (V,A) 更新處由旗標把關：
HOSTILE_AFFECT_FIX（config 預設 True／monitor 端 getattr 預設 False＝既有測試假 cfg 無此欄＝逐位元同現狀）。
affect_delta_for 本體一字不動（fingerprint／test_circumplex 位元不變）。全 stub、零網路。
"""

import os
import re
import tempfile
import unittest
from datetime import datetime, timezone
from types import SimpleNamespace
from zoneinfo import ZoneInfo

from telegram_monitor import monitor, reaction
from telegram_monitor.state import State

TZ = ZoneInfo("Asia/Taipei")
NOW = datetime(2026, 7, 10, 4, 9, 0, tzinfo=timezone.utc)   # 台北 12:09（§1.14 截圖時段）

# 汙染句：is_hostile=True 但不在 _CHALLENGE 表 → HEAD 的 affect_delta_for 落「一般陪伴」預設 (+0.05, +0.06)
POLLUTED = ("你太爛了", "我不信", "最好是", "你不要敷衍我", "不想理你了", "隨便你啦")


# ── 單元：reaction.hostile_affect_delta_for（純函式、旗標無關）──────────────
class HostileAffectDeltaTest(unittest.TestCase):
    def test_polluted_sentences_get_challenge_direction(self):
        # 敵意且落預設分支 → 質疑方向（V−A+），與 _CHALLENGE 格完全同值（單一方向、不另立座標）
        challenge = reaction.affect_delta_for("你根本做不到")
        for t in POLLUTED:
            dv, da = reaction.hostile_affect_delta_for(t)
            self.assertLess(dv, 0, t)                      # 被罵 V 不再上升
            self.assertGreater(da, 0, t)                   # 質疑＝緊張（A+），不是低落（A−）
            self.assertEqual((dv, da), challenge, t)

    def test_challenge_table_same_value_as_head(self):
        # 本就走 _CHALLENGE 的句子：原值原樣（改判只補預設分支的縫隙）
        for t in ("你根本做不到", "騙人", "你只是個程式"):
            self.assertEqual(reaction.hostile_affect_delta_for(t), reaction.affect_delta_for(t), t)

    def test_non_hostile_passthrough_bitwise(self):
        # 非敵意句逐位元同 affect_delta_for（暖意/低落/一般/空值全不動）
        for t in ("謝謝你陪我", "我好難過", "今天天氣不錯", "早安", "", None):
            self.assertEqual(reaction.hostile_affect_delta_for(t), reaction.affect_delta_for(t), repr(t))

    def test_classified_mixed_sentence_keeps_original_class(self):
        # 消融定案：詞表已分類的敵意混合句（「謝謝你都敷衍我」→ 暖意格）維持原分類——
        # 本函式只補「落預設分支」的縫隙；反諷類語意細判留給 LLM 層，不再堆啟發式（詞表窮舉 15 次前科）。
        self.assertTrue(reaction.is_hostile("謝謝你都敷衍我"))
        self.assertEqual(reaction.hostile_affect_delta_for("謝謝你都敷衍我"),
                         reaction.affect_delta_for("謝謝你都敷衍我"))

    def test_head_pollution_pinned_in_original(self):
        # 釘住 affect_delta_for 本體不動（fingerprint／旗標關對照的根）：敵意句在原函式仍回一般陪伴微暖
        for t in POLLUTED:
            self.assertEqual(reaction.affect_delta_for(t), (0.05, 0.06), t)


# ── 整合：handle_message 的 (V,A) 更新（harness 同 test_mood_coord_report）──
class MoodIntegrationTest(unittest.TestCase):
    class Cl:
        def __init__(self): self.sent, self.dry_run = [], False
        def send(self, t): self.sent.append(t); return True

    def _cfg(self, fix, coord=False):
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
                    promise_deliver_content_enabled=False, mood_coord_report_enabled=coord)
        if fix is not None:                                # None＝欄位缺席（既有測試假 cfg 的樣子）
            base["hostile_affect_fix_enabled"] = fix
        return SimpleNamespace(**base)

    def _coach(self):
        c = SimpleNamespace(enabled=True, api_key="k", model="m",
                            meter=SimpleNamespace(record=lambda *a, **k: None))
        c.ask = lambda *a, **k: ("chat", None, "嗯。")
        c.reply = lambda *a, **k: "嗯。"
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

    def _run(self, text, fix, coord=False):
        s = State(os.path.join(tempfile.mkdtemp(), "s.json"))
        s.owner_folder_id = "F"
        s.entropy = SimpleNamespace(mood=0.0, arousal=0.0, hunger=0.5, self_stims_this_idle=0,
                                    reach_outs_this_idle=0, coping_reach_outs_this_idle=0)
        monitor.handle_message({"message": {"chat": {"id": 1}, "text": text, "date": NOW.timestamp()}},
                               self._coach(), self.BOOM, {"meta": {}, "records": []}, self.SNAP,
                               s, self.Cl(), self._cfg(fix, coord), TZ)
        return s

    def test_fix_on_scolded_mood_drops_arousal_rises(self):
        # §1.45 repro case 1 的修復本體：被罵 → V 往下（質疑）、A 往上（緊張），不再「被罵反而變暖」
        s = self._run("你太爛了", fix=True)
        self.assertLess(s.entropy.mood, 0.0)
        self.assertGreater(s.entropy.arousal, 0.0)

    def test_flag_off_bitwise_head_pollution_preserved(self):
        # 【消融】旗標關＝同現狀（汙染保留：被罵 mood 仍微升）；欄位缺席（既有測試假 cfg）＝同關
        off = self._run("你太爛了", fix=False)
        self.assertGreater(off.entropy.mood, 0.0)
        absent = self._run("你太爛了", fix=None)
        self.assertEqual(absent.entropy.mood, off.entropy.mood)
        self.assertEqual(absent.entropy.arousal, off.entropy.arousal)

    def test_fix_on_non_hostile_bitwise_same(self):
        # 旗標開、非敵意句＝逐位元同旗標關（passthrough：只動敵意預設分支那個縫隙）
        for t in ("謝謝你陪我", "今天天氣不錯"):
            on, off = self._run(t, fix=True), self._run(t, fix=False)
            self.assertEqual(on.entropy.mood, off.entropy.mood, t)
            self.assertEqual(on.entropy.arousal, off.entropy.arousal, t)

    def test_trace_cause_and_numbers_now_agree(self):
        # §1.45 同輪一致性：軌跡寫「被說了重話」時 v 也真的往下＝數字與說法不再自相矛盾
        s = self._run("你太爛了", fix=True, coord=True)
        self.assertTrue(s.mood_trace)
        self.assertIn("被說了重話", s.mood_trace[-1]["cause"])
        self.assertLess(s.mood_trace[-1]["v"], 0.0)


# ── 同步：config／.env.example／README ──────────────────────────────────────
class ConfigTest(unittest.TestCase):
    def test_config_synced(self):
        src = open("telegram_monitor/config.py", encoding="utf-8").read()
        self.assertIn("HOSTILE_AFFECT_FIX", src)
        self.assertIn("hostile_affect_fix_enabled", src)
        env = open(".env.example", encoding="utf-8").read()
        self.assertRegex(env, re.compile(r"^HOSTILE_AFFECT_FIX=1", re.M))
        self.assertIn("HOSTILE_AFFECT_FIX", open("README.md", encoding="utf-8").read())

    def test_getattr_defaults_false(self):
        self.assertFalse(getattr(SimpleNamespace(), "hostile_affect_fix_enabled", False))


if __name__ == "__main__":
    unittest.main()
