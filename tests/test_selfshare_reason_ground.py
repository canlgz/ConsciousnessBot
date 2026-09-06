"""🍃 §1.38 自陳理由接地（SELFSHARE_REASON_GROUND）：bot 被追問「為什麼剛剛那樣說」時，據**真實理由**誠實作答，
不因無接地而漂到別處腦補。

截圖根因：bot 主動發了 🍃 環境換檔自陳「沒什麼新動靜，我先把節奏調慢、輕輕待著就好。」（environ.report_line("quieting")，
理由＝周遭活絡度轉冷清／資料面沒新東西 → 主動調慢轉速）。使用者追問「為什麼會沒有動靜？」——這句無「你/妳」→ §0.85
的 is_selfshare_followup（刻意要求含你/妳的反劫持窄門）**不收** → 落 fact_or_chat。該 lane 看得到那句 🍃 原話（convo_history
有記、跨重生存活），**卻拿不到它背後的環境理由**（理由只在 🩺 目前狀態 self-state brief）→ LLM 無接地、漂到前文的貼圖話題、
宣稱送了一張 → 被 §1.34 假送閘攔成「我其實沒真的送出貼圖…要我送一張嗎？」＝答非所問。§1.36 的 sibling（回想自己主動說過
的話的原因也別編）。

修法（比照 §1.36 兩層旗標分離、命中才注入）：
 (1) 🍃 換檔自陳送出後、旗標開 → 把**真實理由**（environ.shift_reason）附到 state.last_selfshare（旗標關＝不附＝同現狀）。
 (2) fact_or_chat 的 coach.ask extra_system 追加一格 _selfshare_reason_hint：近期自陳＋窗內＋那則是 bot 最後一句（相關性綁定
     比照 §0.85）＋這句含 why 標記 → 注入真實理由接地（含「別扯到你沒真的做過的事，例如送貼圖」）；否則 ""＝逐位元同現狀。
全 stub、零網路。
"""

import os
import re
import tempfile
import unittest
from datetime import datetime, timezone
from types import SimpleNamespace
from zoneinfo import ZoneInfo

from telegram_monitor import monitor, persona, environ, referent
from telegram_monitor.state import State

TZ = ZoneInfo("Asia/Taipei")
NOW = datetime(2026, 7, 13, 4, 8, 0, tzinfo=timezone.utc)   # 台北 12:08
SHARE_LINE = "沒什麼新動靜，我先把節奏調慢、輕輕待著就好。"
LAST_MODEL = "🍃 " + SHARE_LINE


class ShiftReasonTest(unittest.TestCase):
    def test_active_shifts_have_reason(self):
        for shift in ("quieting", "livening"):
            r = environ.shift_reason(shift)
            self.assertTrue(r)
            self.assertIn("貼圖", r)          # 明確把「跟送不送貼圖無關」寫進理由，止住漂移

    def test_non_active_shift_empty(self):
        for shift in ("nightfall", "daybreak", None, "", "bogus"):
            self.assertEqual(environ.shift_reason(shift), "")


class PersonaTest(unittest.TestCase):
    def test_reason_system_exists_and_steers(self):
        self.assertTrue(hasattr(persona, "selfshare_reason_system"))
        s = persona.selfshare_reason_system(SHARE_LINE, "周遭安靜下來了")
        self.assertIn("周遭安靜下來了", s)
        self.assertIn("貼圖", s)              # 明令別扯到沒真做過的事（例如送貼圖）


class StashReasonTest(unittest.TestCase):
    """(1) 換檔自陳送出後的理由附掛：旗標開＋活絡換檔→附；旗標關/非活絡換檔→不附（同現狀）。"""

    def _state(self):
        st = State(os.path.join(tempfile.mkdtemp(), "s.json"))
        st.last_selfshare = {"text": SHARE_LINE, "ts": NOW.timestamp()}
        return st

    def test_flag_on_attaches_reason(self):
        st = self._state()
        monitor._stash_selfshare_reason(st, SimpleNamespace(selfshare_reason_ground_enabled=True), "quieting")
        self.assertTrue(st.last_selfshare.get("reason"))

    def test_flag_off_no_reason_key(self):
        st = self._state()
        monitor._stash_selfshare_reason(st, SimpleNamespace(selfshare_reason_ground_enabled=False), "quieting")
        self.assertNotIn("reason", st.last_selfshare)     # 逐位元同現狀（dict 形狀不變）

    def test_non_active_shift_no_reason(self):
        st = self._state()
        monitor._stash_selfshare_reason(st, SimpleNamespace(selfshare_reason_ground_enabled=True), "nightfall")
        self.assertNotIn("reason", st.last_selfshare)


class ReasonHintTest(unittest.TestCase):
    """(2) _selfshare_reason_hint：happy path 注入；旗標關/無理由/過窗/無 why/相關性不符 → ''。"""

    def _state(self, reason="周遭安靜下來了、也沒新東西進來，我把節奏調慢", ts_offset=-60, last_model=LAST_MODEL):
        st = State(os.path.join(tempfile.mkdtemp(), "s.json"))
        st.last_selfshare = {"text": SHARE_LINE, "ts": NOW.timestamp() + ts_offset}
        if reason is not None:
            st.last_selfshare["reason"] = reason
        st.convo_history = [{"role": "user", "text": "嗯", "ts": 0},
                            {"role": "model", "text": last_model, "ts": 0}]
        return st

    def _cfg(self, on=True):
        return SimpleNamespace(selfshare_reason_ground_enabled=on)

    def test_happy_path_injects(self):
        st = self._state()
        out = monitor._selfshare_reason_hint(st, self._cfg(), "為什麼會沒有動靜？", NOW.timestamp())
        self.assertIn("周遭安靜下來了", out)
        self.assertIn("貼圖", out)

    def test_flag_off_empty(self):
        st = self._state()
        self.assertEqual(monitor._selfshare_reason_hint(st, self._cfg(on=False), "為什麼會沒有動靜？", NOW.timestamp()), "")

    def test_no_reason_empty(self):
        st = self._state(reason=None)
        self.assertEqual(monitor._selfshare_reason_hint(st, self._cfg(), "為什麼會沒有動靜？", NOW.timestamp()), "")

    def test_past_window_empty(self):
        st = self._state(ts_offset=-(referent.FOLLOWUP_WINDOW_SEC + 60))
        self.assertEqual(monitor._selfshare_reason_hint(st, self._cfg(), "為什麼會沒有動靜？", NOW.timestamp()), "")

    def test_no_why_marker_empty(self):
        st = self._state()
        self.assertEqual(monitor._selfshare_reason_hint(st, self._cfg(), "好喔我知道了", NOW.timestamp()), "")

    def test_relevance_not_bound_empty(self):
        # bot 最後一句不是那則自陳（中間插了別的回覆）→ 不注入（防無關 why 句劫持，比照 §0.85）
        st = self._state(last_model="我們聊點別的吧")
        self.assertEqual(monitor._selfshare_reason_hint(st, self._cfg(), "為什麼會沒有動靜？", NOW.timestamp()), "")

    def test_unrelated_why_right_after_share_empty(self):
        # 剛好跟在自陳後、含 why 詞卻無關的句子（「為什麼天空是藍的」）→ 內容無重疊 → 不注入（防劫持）
        st = self._state()
        self.assertEqual(monitor._selfshare_reason_hint(st, self._cfg(), "為什麼天空是藍的？", NOW.timestamp()), "")

    def test_relevant_overlap_helper(self):
        self.assertTrue(monitor._selfshare_relevant("為什麼會沒有動靜？", SHARE_LINE))     # 動靜 重疊
        self.assertFalse(monitor._selfshare_relevant("為什麼天空是藍的？", SHARE_LINE))    # 剝 why 後無重疊（不靠「什麼」假重疊）


class EndToEndTest(unittest.TestCase):
    """經真 handle_message：近期 🍃 自陳（帶 reason）＋追問「為什麼會沒有動靜」→ fact_or_chat 的 coach.ask
    extra_system 收到真實理由接地；旗標關＝不注入。"""

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
                    selfshare_followup_enabled=True, selfshare_reason_ground_enabled=on)
        return SimpleNamespace(**base)

    def _coach(self):
        seen = {"ask": None}
        c = SimpleNamespace(enabled=True, api_key="k", model="m",
                            meter=SimpleNamespace(record=lambda *a, **k: None), seen=seen)
        c.ask = lambda *a, **k: (seen.__setitem__("ask", k.get("extra_system")), ("chat", None, "嗯，我剛把節奏放慢了。"))[1]
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
    BOOM = SimpleNamespace(load_embedding_records=lambda *_: None)

    def _run(self, on=True):
        s = State(os.path.join(tempfile.mkdtemp(), "s.json"))
        s.owner_folder_id = "F"
        s.last_selfshare = {"text": SHARE_LINE, "ts": NOW.timestamp() - 60,
                            "reason": "周遭安靜下來了、也沒新東西進來，我把節奏調慢，這跟送不送貼圖無關"}
        s.convo_history = [{"role": "model", "text": LAST_MODEL, "ts": NOW.timestamp() - 60}]
        co = self._coach()
        cl = SimpleNamespace(sent=[], dry_run=False, send=lambda t: (co.seen, cl.sent.append(t))[1] or True)
        monitor.handle_message({"message": {"chat": {"id": 1}, "text": "為什麼會沒有動靜？",
                                            "date": NOW.timestamp()}},
                               co, self.BOOM, {"meta": {}, "records": []}, self.SNAP, s, cl, self._cfg(on), TZ)
        return co.seen["ask"]

    def test_reason_injected_into_ask(self):
        extra = self._run(on=True) or ""
        self.assertIn("周遭安靜下來了", extra)
        self.assertIn("貼圖", extra)              # 明令別扯貼圖＝止住截圖的漂移

    def test_flag_off_not_injected(self):
        extra = self._run(on=False) or ""
        self.assertNotIn("周遭安靜下來了", extra)


class ConfigSyncTest(unittest.TestCase):
    def test_flag_declared_and_synced(self):
        src = open("telegram_monitor/config.py", encoding="utf-8").read()
        self.assertIn("SELFSHARE_REASON_GROUND", src)
        self.assertIn("selfshare_reason_ground_enabled", src)
        env = open(".env.example", encoding="utf-8").read()
        self.assertRegex(env, re.compile(r"^SELFSHARE_REASON_GROUND=1", re.M))
        readme = open("README.md", encoding="utf-8").read()
        self.assertIn("SELFSHARE_REASON_GROUND", readme)

    def test_monitor_getattr_defaults_false(self):
        self.assertFalse(getattr(SimpleNamespace(), "selfshare_reason_ground_enabled", False))


if __name__ == "__main__":
    unittest.main()
