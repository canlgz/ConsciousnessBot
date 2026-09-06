"""🤝🧠 §1.12 LLM 語意逃生閘：確定性捕捉 miss＋temporal 有未來錨時，單次 gemini 判
「是否請 bot 到點做事＋動作命名」——**時刻永遠來自 temporal／程式時鐘，LLM 永遠不准產出時刻**
（幻覺時刻 11:08 正是讓 LLM 碰時間的下場；協定上沒有時間欄位、輸出裡的時刻字樣程式端一律丟棄）。

截圖（2026-07-10 12:08）：「我跟你對談一下，10 分鐘後，再告訴我你的心情」被句首「我」啟發式殺死、
降級成無鐘點的 feeling promise → ack 沒講鐘點、12:18 什麼都沒發生、12:28 被催才假兌現。
逃生閘只在結構閘開火但確定性捕捉沒接到時呼叫一次（不進每則訊息熱路徑）；judge 回「否」＝自然聊天
（守則不掛）；judge 失敗＝安全退回 §1.09 誠實守門。旗標 PROMISE_LLM_RESCUE=0＝逃生閘不存在＝同現狀。
全 stub、絕不打網路。
"""

import os
import tempfile
import unittest
from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import patch
from zoneinfo import ZoneInfo

from telegram_monitor import gemini, monitor, persona, selfstate
from telegram_monitor.state import State

TZ = ZoneInfo("Asia/Taipei")
NOW = datetime(2026, 7, 10, 4, 8, 0, tzinfo=timezone.utc)    # 台北 12:08（截圖 #2 時刻）
NOW1 = datetime(2026, 7, 10, 3, 43, 0, tzinfo=timezone.utc)  # 台北 11:43（截圖 #1 時刻）

S2 = "我跟你對談一下，10 分鐘後，再告訴我你的心情"
S1 = "別廢話了，20 分鐘後，再告訴我你不同的地方在哪裡？"
MEETING = "20分鐘後我要開會，你覺得呢"


class FakeClient:
    def __init__(self):
        self.sent, self.stickers, self.dry_run = [], [], False

    def send(self, text):
        self.sent.append(text)
        return True

    def send_sticker(self, file_id):
        self.stickers.append(file_id)
        return True


def _coach(judge=None):
    """假教練（照 tests/test_deferred_promise.py 樣式）：judge 可控回值＋呼叫計數；零網路。"""
    c = SimpleNamespace(
        enabled=True, api_key="k", model="m",
        meter=SimpleNamespace(record=lambda *a, **k: None),
        judge_calls=[],
        voice_schedule_ack=None, voice_promise_ack=None, judge_timed_request=None,
        acks=[])

    def _ack(q, when, h, sticker_hint=""):
        c.acks.append(when)
        return f"好，{when.strftime('%H:%M') if when else '到時候'}我會來跟你說。"

    def _judge(text):
        c.judge_calls.append(text)
        if judge is None:
            return None
        return judge(text)

    c.voice_schedule_ack = _ack
    c.voice_promise_ack = lambda q, h: "好，真的有感覺上來，我會跟你說。"
    c.judge_timed_request = _judge
    return c


def _cfg(**over):
    base = dict(dry_run=False, telegram_chat_id="", scheduled_promise_enabled=True,
                promise_emit_enabled=True, promise_sched_ttl_sec=21600, timezone="Asia/Taipei",
                notify_cooldown_min=30, promise_reply_bridge_enabled=True, promise_ledger_enabled=True,
                sched_leave_autoarm_enabled=True, deferred_promise_enabled=True,
                promise_llm_rescue_enabled=True)
    base.update(over)
    return SimpleNamespace(**base)


def _msg(text, when):
    return {"message": {"chat": {"id": 1}, "text": text, "date": when.timestamp()}}


_BOOM = SimpleNamespace(load_embedding_records=lambda *_: (_ for _ in ()).throw(AssertionError("逃生閘路徑不該進一般聊天")))


class RescueE2ETest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        monitor._TURN.pop("promise_guard_llm_no", None)   # 隔離跨測試殘留

    def _state(self):
        s = State(os.path.join(self.tmp, "s.json"))
        s.owner_folder_id = "F"
        return s

    def test_red_rescue_books_scheduled_not_feeling(self):
        """【紅】SCHED_HEAD_ME_FIX=0（模擬 §1.11 未修或又漏新說法）＋judge 是 → 入帳 12:18、不落 feeling。"""
        st, cl = self._state(), FakeClient()
        co = _coach(judge=lambda t: (True, "跟他說說此刻的心情"))
        with patch.dict(os.environ, {"SCHED_HEAD_ME_FIX": "0"}):
            monitor.handle_message(_msg(S2, NOW), co, _BOOM, {"meta": {}}, None, st, cl, _cfg(), TZ)
        proms = st.scheduled_promises or []
        self.assertEqual(len(proms), 1)
        self.assertEqual(round(proms[0]["target_ts"]), round(NOW.timestamp() + 600))   # temporal 算的 12:18
        self.assertEqual(proms[0].get("status"), "pending")
        self.assertFalse(proms[0].get("fulfilled"))
        self.assertIsNone(getattr(st, "feeling_promise", None))          # 沒被降級成無鐘點帳
        self.assertEqual(len(co.judge_calls), 1)                          # 同一則訊息至多一次
        self.assertEqual(len(co.acks), 1)                                 # ack 走 voice_schedule_ack
        self.assertEqual(co.acks[0].strftime("%H:%M"), "12:18")           # 必帶程式時鐘算的鐘點

    def test_llm_clock_in_action_discarded(self):
        """【鐵律】judge 的動作字串夾了時刻 → target 仍是 temporal 的 12:18、behavior 不含 11:08。"""
        st, cl = self._state(), FakeClient()
        co = _coach(judge=lambda t: (True, "在11:08告訴他心情"))
        with patch.dict(os.environ, {"SCHED_HEAD_ME_FIX": "0"}):
            monitor.handle_message(_msg(S2, NOW), co, _BOOM, {"meta": {}}, None, st, cl, _cfg(), TZ)
        proms = st.scheduled_promises or []
        self.assertEqual(len(proms), 1)
        self.assertEqual(round(proms[0]["target_ts"]), round(NOW.timestamp() + 600))
        self.assertNotIn("11:08", proms[0].get("behavior") or "")

    def test_behavior_override_only_when_extraction_empty(self):
        """確定性 extract 抽不出行為時才用 LLM 動作（已淨化）；時刻由 temporal。"""
        st, cl = self._state(), FakeClient()
        co = _coach(judge=lambda t: (True, "表演一段給他看"))
        text = "35分鐘後，你再表演一段給我看"
        self.assertEqual(selfstate.extract_promise_behavior(text), "")   # 前提：動詞表真的抽不出
        ok = monitor._maybe_llm_promise_rescue(cl, st, _cfg(), co, SimpleNamespace(kind="fact_or_chat"),
                                               text, NOW, TZ, NOW.timestamp())
        self.assertTrue(ok)
        proms = st.scheduled_promises or []
        self.assertEqual(len(proms), 1)
        self.assertEqual(proms[0].get("behavior"), "表演一段給他看")
        self.assertEqual(round(proms[0]["target_ts"]), round(NOW.timestamp() + 35 * 60))

    def test_judge_no_means_natural_chat_no_guard(self):
        """【否】judge 裁決「不是請 bot」→ 無入帳、本輪守則不掛（''）；下一輪 _TURN 重設恢復正常。"""
        st, cl, cfg = self._state(), FakeClient(), _cfg()
        co = _coach(judge=lambda t: (False, ""))
        ok = monitor._maybe_llm_promise_rescue(cl, st, cfg, co, SimpleNamespace(kind="fact_or_chat"),
                                               MEETING, NOW, TZ, NOW.timestamp())
        self.assertFalse(ok)
        self.assertEqual(len(st.scheduled_promises or []), 0)
        self.assertEqual(len(co.judge_calls), 1)
        self.assertEqual(monitor._promise_guard_hint(st, cfg, MEETING), "")   # HEAD 回 PROMISE_ACK_GUARD＝紅
        # 下一輪（任何新訊息輪開頭都會清 _TURN 標記）→ 守則恢復正常
        cfg2 = _cfg(telegram_chat_id="9")   # 非擁有者訊息＝早退輪，但輪首已重置 _TURN
        monitor.handle_message(_msg("隨便聊聊", NOW), co, _BOOM, {"meta": {}}, None, st, cl, cfg2, TZ)
        self.assertEqual(monitor._promise_guard_hint(st, cfg, MEETING), persona.PROMISE_ACK_GUARD)

    def test_judge_failure_falls_back_to_guard(self):
        """【失敗退回 §1.09】GeminiError／None → 不入帳、不設標記＝誠實守則照掛（安全退回）。"""
        st, cl, cfg = self._state(), FakeClient(), _cfg()

        def _boomjudge(t):
            raise gemini.GeminiError("網路炸了")
        for j in (_boomjudge, lambda t: None):
            monitor._TURN.pop("promise_guard_llm_no", None)
            co = _coach(judge=j)
            ok = monitor._maybe_llm_promise_rescue(cl, st, cfg, co, SimpleNamespace(kind="fact_or_chat"),
                                                   MEETING, NOW, TZ, NOW.timestamp())
            self.assertFalse(ok)
            self.assertEqual(len(st.scheduled_promises or []), 0)
            self.assertEqual(monitor._promise_guard_hint(st, cfg, MEETING), persona.PROMISE_ACK_GUARD)

    def test_no_call_for_emergence_or_no_anchor(self):
        """【不誤呼叫】湧現條件託付照走 feeling；無時間錨不呼叫；帶錨的湧現託付也不呼叫。"""
        st, cl, cfg = self._state(), FakeClient(), _cfg()
        co = _coach(judge=lambda t: (True, "不該被呼叫"))
        monitor.handle_message(_msg("明天有感覺再跟我說", NOW), co, _BOOM, {"meta": {}}, None, st, cl, cfg, TZ)
        self.assertEqual(len(co.judge_calls), 0)
        self.assertIsNotNone(st.feeling_promise)                          # 照走 feeling＝現狀
        self.assertEqual(len(st.scheduled_promises or []), 0)
        # 無時間錨（gate ③）→ 不呼叫
        ok = monitor._maybe_llm_promise_rescue(cl, st, cfg, co, SimpleNamespace(kind="smalltalk"),
                                               "你好啊", NOW, TZ, NOW.timestamp())
        self.assertFalse(ok)
        self.assertEqual(len(co.judge_calls), 0)
        # 帶錨的「有感覺才說」（gate ⑤）→ 不呼叫＝不為交差假裝的不變式
        ok = monitor._maybe_llm_promise_rescue(cl, st, cfg, co, SimpleNamespace(kind="promise"),
                                               "10分鐘後有感覺再跟我說", NOW, TZ, NOW.timestamp())
        self.assertFalse(ok)
        self.assertEqual(len(co.judge_calls), 0)

    def test_deterministic_capture_zero_calls_and_same_bits(self):
        """截圖 #1（捕捉成功走 scheduled）→ judge 呼叫數 0，入帳/ack 與 HEAD 位元相同（釘 refactor 無漂移）。"""
        st, cl = self._state(), FakeClient()
        co = _coach(judge=lambda t: (True, "不該被呼叫"))
        monitor.handle_message(_msg(S1, NOW1), co, _BOOM, {"meta": {}}, None, st, cl, _cfg(), TZ)
        self.assertEqual(len(co.judge_calls), 0)
        proms = st.scheduled_promises or []
        self.assertEqual(len(proms), 1)
        self.assertEqual(round(proms[0]["target_ts"]), round(NOW1.timestamp() + 1200))   # 12:03
        self.assertEqual(proms[0].get("status"), "pending")
        self.assertEqual(proms[0].get("action"), S1[:120])
        self.assertEqual(proms[0].get("made_text"), S1[:200])
        self.assertEqual(len(co.acks), 1)
        self.assertEqual(co.acks[0].strftime("%H:%M"), "12:03")

    def test_flag_off_bitwise_head_behavior(self):
        """【旗標消融】PROMISE_LLM_RESCUE=0 → 呼叫數 0、#2 句落 feeling_promise＝HEAD 位元行為。"""
        st, cl = self._state(), FakeClient()
        co = _coach(judge=lambda t: (True, "不該被呼叫"))
        with patch.dict(os.environ, {"SCHED_HEAD_ME_FIX": "0"}):
            monitor.handle_message(_msg(S2, NOW), co, _BOOM, {"meta": {}}, None, st, cl,
                                   _cfg(promise_llm_rescue_enabled=False), TZ)
        self.assertEqual(len(co.judge_calls), 0)
        self.assertIsNotNone(st.feeling_promise)
        self.assertEqual(len(st.scheduled_promises or []), 0)


class SanitizeTest(unittest.TestCase):
    """_sanitize_llm_action：LLM 輸出裡任何時刻字樣一律丟棄（11:08 幻覺前科）。"""

    def test_clock_and_duration_stripped(self):
        self.assertNotIn("11:08", monitor._sanitize_llm_action("在11:08告訴他心情"))
        self.assertNotIn("3分鐘", monitor._sanitize_llm_action("3分鐘後提醒他"))
        self.assertNotIn("8點", monitor._sanitize_llm_action("8點跟他打招呼"))
        self.assertNotIn("時刻", monitor._sanitize_llm_action("到了那個時刻跟他說"))
        self.assertNotIn("幾點", monitor._sanitize_llm_action("跟他說現在幾點"))

    def test_clean_action_untouched(self):
        self.assertEqual(monitor._sanitize_llm_action("跟他說說此刻的心情"), "跟他說說此刻的心情")

    def test_empty_when_nothing_left(self):
        self.assertEqual(monitor._sanitize_llm_action("11:08"), "")
        self.assertEqual(monitor._sanitize_llm_action(""), "")

    def test_truncated_to_40(self):
        self.assertLessEqual(len(monitor._sanitize_llm_action("好" * 80)), 40)


class JudgeProtocolTest(unittest.TestCase):
    """persona 協定：沒有時間欄位＝LLM 無從回傳時刻；指令明講嚴禁輸出時刻。"""

    def test_system_prompt_forbids_time(self):
        g = persona.PROMISE_RESCUE_JUDGE_SYSTEM
        self.assertIn("嚴禁", g)
        self.assertIn("時間由系統時鐘管理", g)
        self.assertIn("是", g)
        self.assertIn("否", g)

    def test_user_prompt_contains_text(self):
        self.assertIn("10 分鐘後", persona.promise_rescue_judge_user(S2))


if __name__ == "__main__":
    unittest.main()
