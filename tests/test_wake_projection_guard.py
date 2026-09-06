"""🌅 §1.39 自他邊界守門（WAKE_PROJECTION_GUARD）：bot 不再把**自己**剛睡醒/悶悶的狀態投射成使用者的。

截圖根因：bot 重生時寫下第一人稱 🦋/🌅 醒來敘事（continuity.wake_line「我親身記得…這一覺睡了一下」，mood=悶）進
convo_history（role=model、角色標對）。使用者問「什麼新方法？」時，聊天 LLM 卻把 bot **自己**的醒來＋心情投射成
使用者：「你醒了，但現在是晚上七點多耶，你剛剛小睡了一下，感覺還帶著悶悶的感覺，這樣不會太晚睡嗎？」——使用者
根本沒說自己睡醒/小睡。使用者回「我醒了？你瘋了嗎」，bot 才承認「我以為你是真的剛睡醒…搞錯了很基本的狀況判斷」。
＝自我/他人邊界洩漏（self→other 投射），§1.13B/§1.36 的 sibling。「悶悶/悶」在 affect.py/circumplex.py 是 **bot 自己**的
情緒座標詞。

修法（比照 §1.13B/§1.36：arm 於 handle_message、判定/替換於 _say 唯一 lane-agnostic 出口、只讀 _TURN、不呼叫 LLM）：
 - 只在 bot 這條命是重生來的（state.waking 非 first）＋旗標開＋使用者近期**沒有**自述睡醒/小睡（grounding）時 arm。
 - _say 互動出口：把回覆裡「斷言使用者剛睡醒/小睡了一下/醒了」的句子剝掉（引用歸屬「你說你剛睡醒」不剝、bot 講
   自己「我剛睡醒」不剝、祈使「你醒醒吧」不剝）；整則都是投射→換成誠實更正句。
 - 命中才注入的 persona.WAKE_BOUNDARY_HINT（fact_or_chat）當事前預防。旗標關＝不 arm＝逐位元同現狀。全 stub、零網路。
"""

import os
import re
import tempfile
import unittest
from datetime import datetime, timezone
from types import SimpleNamespace
from zoneinfo import ZoneInfo

from telegram_monitor import monitor, persona
from telegram_monitor.state import State

TZ = ZoneInfo("Asia/Taipei")
NOW = datetime(2026, 7, 13, 11, 8, 0, tzinfo=timezone.utc)   # 台北 19:08

PROJ = "你醒了，但現在是晚上七點多耶，你剛剛小睡了一下，感覺還帶著悶悶的感覺，這樣不會太晚睡嗎？"
PROJ_MIXED = "你剛剛小睡了一下吧？其實我那圈生命迴圈一直在轉，會把每個字跟你的記寫對照。"
WAKING = {"first": False, "gap_s": 300, "gap_label": "一下子", "lead": None, "contents": [], "faded": False}


class StripTest(unittest.TestCase):
    def test_whole_projection_becomes_correction(self):
        out, ch = monitor._strip_wake_projection(PROJ)
        self.assertTrue(ch)
        self.assertNotIn("你醒了", out)
        self.assertNotIn("你剛剛小睡", out)
        self.assertNotIn("悶悶的", out)

    def test_mixed_keeps_real_answer(self):
        out, ch = monitor._strip_wake_projection(PROJ_MIXED)
        self.assertTrue(ch)
        self.assertNotIn("你剛剛小睡", out)
        self.assertIn("生命迴圈", out)              # 真正的答案留著

    def test_bot_first_person_not_stripped(self):
        # bot 講**自己**剛睡醒（第一人稱）＝正確、不剝
        t = "我剛睡醒，腦子還有點鈍，你說。"
        out, ch = monitor._strip_wake_projection(t)
        self.assertFalse(ch)
        self.assertEqual(out, t)

    def test_quote_attribution_not_stripped(self):
        # 引用歸屬（複述對方）＝不剝
        t = "你說你剛睡醒，那你還好嗎？"
        out, ch = monitor._strip_wake_projection(t)
        self.assertFalse(ch)

    def test_imperative_wake_not_stripped(self):
        # 「你醒醒吧」（祈使、清醒點）≠「你醒了」（斷言剛睡醒）
        t = "你醒醒吧，別鑽牛角尖了。"
        out, ch = monitor._strip_wake_projection(t)
        self.assertFalse(ch)

    def test_no_projection_unchanged(self):
        t = "其實我那圈生命迴圈一直在轉，會把每個字跟你的記寫對照。"
        out, ch = monitor._strip_wake_projection(t)
        self.assertFalse(ch)
        self.assertEqual(out, t)


class GroundingTest(unittest.TestCase):
    def test_user_reported_sleep_true(self):
        self.assertTrue(monitor._user_reported_sleep([], "我剛睡醒，好累"))
        self.assertTrue(monitor._user_reported_sleep(
            [{"role": "user", "text": "我小睡了一下", "ts": 0}], "現在幾點"))

    def test_user_no_sleep_false(self):
        self.assertFalse(monitor._user_reported_sleep(
            [{"role": "user", "text": "什麼新方法？", "ts": 0}], "什麼新方法？"))

    def test_bot_sleep_not_grounding(self):
        # bot 自己的「我剛睡醒」(role=model) 不算使用者 grounding
        self.assertFalse(monitor._user_reported_sleep(
            [{"role": "model", "text": "我剛睡醒", "ts": 0}], "什麼新方法？"))


class SayGuardTest(unittest.TestCase):
    class Cl:
        def __init__(self):
            self.sent, self.dry_run = [], False

        def send(self, text):
            self.sent.append(text)
            return True

    def setUp(self):
        monitor._TURN.clear()
        monitor._TURN["bubbles"] = None

    def _out(self, text, **turn):
        monitor._TURN.update(turn)
        cl = self.Cl()
        monitor._say(cl, text)
        return "".join(cl.sent)

    def test_armed_strips(self):
        out = self._out(PROJ, wake_proj_strip=True)
        self.assertNotIn("你剛剛小睡", out)
        self.assertNotIn("你醒了", out)

    def test_not_armed_passthrough(self):
        out = self._out(PROJ)
        self.assertIn("你剛剛小睡", out)             # 未 arm＝逐位元同現狀


class EndToEndTest(unittest.TestCase):
    def _cfg(self, on=True):
        base = dict(dry_run=False, telegram_chat_id="", scheduled_promise_enabled=True,
                    promise_emit_enabled=True, promise_sched_ttl_sec=21600, timezone="Asia/Taipei",
                    notify_cooldown_min=30, promise_reply_bridge_enabled=False, promise_ledger_enabled=True,
                    sched_leave_autoarm_enabled=True, deferred_promise_enabled=True,
                    promise_llm_rescue_enabled=False, sticker_llm_rescue_enabled=False,
                    promise_keep_claim_guard_enabled=True, promise_said_ground_enabled=True,
                    bot_self_promise_enabled=False, promise_preempt_enabled=False,
                    sticker_sent_memory_enabled=True, send_stickers=True,
                    sticker_fakesend_guard_enabled=False, recall_ground_guard_enabled=True,
                    selfshare_reason_ground_enabled=False, wake_projection_guard_enabled=on)
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
    BOOM = SimpleNamespace(load_embedding_records=lambda *_: None)

    def _run(self, question, voice, on=True, waking=WAKING, history=None):
        s = State(os.path.join(tempfile.mkdtemp(), "s.json"))
        s.owner_folder_id = "F"
        s.waking = dict(waking) if waking else None
        if history:
            s.convo_history = history
        cl = SayGuardTest.Cl()
        monitor.handle_message({"message": {"chat": {"id": 1}, "text": question, "date": NOW.timestamp()}},
                               self._coach(voice), self.BOOM, {"meta": {}, "records": []}, self.SNAP,
                               s, cl, self._cfg(on), TZ)
        return "".join(cl.sent)

    def test_projection_stripped_via_handle_message(self):
        out = self._run("什麼新方法？", PROJ)
        self.assertNotIn("你剛剛小睡", out)
        self.assertNotIn("你醒了", out)

    def test_flag_off_passthrough(self):
        out = self._run("什麼新方法？", PROJ, on=False)
        self.assertEqual(out, PROJ)                  # 旗標關＝byte-identical

    def test_first_birth_not_armed(self):
        # 史上第一次醒（waking.first）＝沒有可投射的醒來敘事 → 不 arm → 原樣送出
        out = self._run("什麼新方法？", PROJ, waking={"first": True})
        self.assertEqual(out, PROJ)

    def test_grounded_when_user_said_sleep(self):
        # 使用者這句自述剛睡醒 → grounded → 不剝（bot 回應使用者說的事是合理的）
        out = self._run("我剛睡醒，好累", PROJ)
        self.assertIn("你剛剛小睡", out)


class HintConfigTest(unittest.TestCase):
    def test_boundary_hint(self):
        self.assertTrue(hasattr(persona, "WAKE_BOUNDARY_HINT"))
        st = SimpleNamespace(waking={"first": False})
        self.assertIn("你自己", monitor._wake_boundary_hint(st, SimpleNamespace(wake_projection_guard_enabled=True)))
        self.assertEqual(monitor._wake_boundary_hint(st, SimpleNamespace(wake_projection_guard_enabled=False)), "")
        self.assertEqual(monitor._wake_boundary_hint(SimpleNamespace(waking=None),
                                                     SimpleNamespace(wake_projection_guard_enabled=True)), "")

    def test_config_synced(self):
        src = open("telegram_monitor/config.py", encoding="utf-8").read()
        self.assertIn("WAKE_PROJECTION_GUARD", src)
        self.assertIn("wake_projection_guard_enabled", src)
        env = open(".env.example", encoding="utf-8").read()
        self.assertRegex(env, re.compile(r"^WAKE_PROJECTION_GUARD=1", re.M))
        self.assertIn("WAKE_PROJECTION_GUARD", open("README.md", encoding="utf-8").read())

    def test_getattr_defaults_false(self):
        self.assertFalse(getattr(SimpleNamespace(), "wake_projection_guard_enabled", False))


if __name__ == "__main__":
    unittest.main()
