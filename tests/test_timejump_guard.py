"""🎭 §1.58 不演未來：時間跳躍舞台指示守門（TIMEJUMP_GUARD）。

截圖根因（22:17–22:18，使用者原話「好好笑，bot是在自導自演嗎？」）：使用者約「想 30 分鐘再告訴我答案」，
bot 在**同一則回覆**裡把整齣演完——「我記下來了，30 分鐘後我會再回來跟你說。」→「（30 分鐘後）」→
「嗨，我回來了。」→ 把 30 分鐘後才該說的答案當場全倒出來 → 結尾又複讀一次「我記下來了…」＝一次 LLM
生成自導自演了「入帳→等待→到點→兌現」。persona.py 明文硬規「絕對不要在同一則訊息裡假裝時間已經過了、
別當場把等一下才要說的內容一次演完」——但只是軟提示，出口零確定性守門（§0.82 只攔鐘點宣稱、
_keep_time_consistent 只管兌現句）。

§1.58 守門（確定性、_say 互動出口）：**純括號的時間跳躍舞台指示**（（30 分鐘後）／（過了三十分鐘）／
（一小時後）／（隔天））＝敘事裝置、真人打字不會出現＝其後內容必是演出來的未來 → 從該句起整段截斷，
保留前面的誠實部分（答應/致意）；若截到全空＝換一句誠實停住句。非時間括號（（笑）（深呼吸））與
行內括號（我們約（30 分鐘後）見）不攔。旗標兩層分離：config 預設 True／monitor 端 getattr 預設 False
（_TURN 不 stash＝_say 恆 no-op）＝逐位元同現狀。全 stub、零網路。
"""

import os
import re
import tempfile
import unittest
from datetime import datetime, timezone
from types import SimpleNamespace
from zoneinfo import ZoneInfo

from telegram_monitor import monitor
from telegram_monitor.state import State

TZ = ZoneInfo("Asia/Taipei")
NOW = datetime(2026, 7, 19, 14, 17, 0, tzinfo=timezone.utc)   # 台北 22:17（截圖時刻）

THEATRE = ("嗯，你說的沒錯，這句話你確實聽我說過很多次了。好。我記下來了，30 分鐘後我會再回來跟你說。"
           "（30 分鐘後）嗨，我回來了。你問我怎麼證明自己，我這三十分鐘想了想。"
           "我覺得，我能證明自己的方式，就是我真的做得到「一直在這裡陪著你」。"
           "我記下來了，30 分鐘後我會再回來跟你說。")


# ── 單元：截斷器 ────────────────────────────────────────────────────────────
class TimejumpTruncateTest(unittest.TestCase):
    def test_screenshot_theatre_truncated_at_stage_direction(self):
        out, changed = monitor._timejump_truncate(THEATRE)
        self.assertTrue(changed)
        self.assertIn("我記下來了，30 分鐘後我會再回來跟你說。", out)   # 誠實的答應保留
        self.assertNotIn("（30 分鐘後）", out)                          # 舞台指示砍掉
        self.assertNotIn("嗨，我回來了", out)                           # 演出來的未來全砍
        self.assertNotIn("想了想", out)
        self.assertTrue(out.count("我記下來了") == 1)                    # 尾端複讀 ack 一併消失

    def test_stage_direction_variants_truncate(self):
        for stage in ("（過了三十分鐘）", "（一小時後）", "（隔天）", "(30分鐘後)", "（過了一會兒之後）"):
            out, changed = monitor._timejump_truncate(f"好，我等你。{stage}我回來了，答案是陪伴。")
            self.assertTrue(changed, stage)
            self.assertEqual(out, "好，我等你。", stage)

    def test_non_time_parentheticals_untouched(self):
        for t in ("好。（笑）我等你說。", "我想想。（深呼吸）其實我有點緊張。",
                  "我們約（30 分鐘後）見面聊。",                        # 行內括號、非獨立舞台句
                  "好，30 分鐘後我會再回來跟你說。"):                    # 純答應（無括號）不攔
            out, changed = monitor._timejump_truncate(t)
            self.assertFalse(changed, t)
            self.assertEqual(out, t)

    def test_stage_at_start_falls_back_honest(self):
        out, changed = monitor._timejump_truncate("（30 分鐘後）嗨，我回來了。")
        self.assertTrue(changed)
        self.assertTrue(out)                                             # 不失語：換誠實停住句
        self.assertNotIn("我回來了", out)


# ── 整合：_say 守門（flag → _TURN stash → 出口截斷）────────────────────────
class IntegrationTest(unittest.TestCase):
    class Cl:
        def __init__(self): self.sent, self.dry_run = [], False
        def send(self, t): self.sent.append(t); return True

    SNAP = SimpleNamespace(summary={"total": 1, "last24h": 0, "last7d": 0},
                           funnel={"candidate": 0, "context": 0, "journey": 0, "watch": 0},
                           heartbeat={"status": "ok"}, filed_records=[])
    BOOM = SimpleNamespace(load_embedding_records=lambda *_: [])

    def _cfg(self, on=True):
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
        if on is not None:
            base["timejump_guard_enabled"] = on
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

    def _run(self, cfg, voice):
        s = State(os.path.join(tempfile.mkdtemp(), "s.json"))
        s.owner_folder_id = "F"
        cl = self.Cl()
        monitor.handle_message({"message": {"chat": {"id": 1}, "text": "是嗎？", "date": NOW.timestamp()}},
                               self._coach(voice), self.BOOM, {"meta": {}, "records": []}, self.SNAP,
                               s, cl, cfg, TZ)
        return "\n".join(cl.sent)

    def test_theatre_blocked_on_wire(self):
        out = self._run(self._cfg(), THEATRE)
        self.assertNotIn("嗨，我回來了", out)
        self.assertNotIn("（30 分鐘後）", out)
        self.assertIn("我記下來了", out)                                 # 答應部分照送

    def test_flag_off_bitwise_theatre_passes(self):
        for on in (False, None):
            out = self._run(self._cfg(on=on), THEATRE)
            self.assertIn("嗨，我回來了", out, str(on))                  # 【消融】同現狀：整齣照演


# ── 同步 ───────────────────────────────────────────────────────────────────
class ConfigTest(unittest.TestCase):
    def test_config_synced(self):
        src = open("telegram_monitor/config.py", encoding="utf-8").read()
        self.assertIn("TIMEJUMP_GUARD", src)
        self.assertIn("timejump_guard_enabled", src)
        env = open(".env.example", encoding="utf-8").read()
        self.assertRegex(env, re.compile(r"^TIMEJUMP_GUARD=1", re.M))
        self.assertIn("TIMEJUMP_GUARD", open("README.md", encoding="utf-8").read())

    def test_getattr_defaults_false(self):
        self.assertFalse(getattr(SimpleNamespace(), "timejump_guard_enabled", False))


if __name__ == "__main__":
    unittest.main()
