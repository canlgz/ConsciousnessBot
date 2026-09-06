"""🤝 §0.75 兩步「延後回答」約定入帳（修「時間感脫離絕對時間」）。

截圖：使用者先「你的內在感覺是怎麼來的？等一下再回答我」（無具體時刻）→ bot 只在對話裡口頭應「我會等一下說」、
沒進帳本、被問又說「我還在等你的指令」；接著「4分鐘後」→ bot 應「好四分鐘後我會說」卻到點什麼都沒發，被問還說
「現在才剛過一分鐘」「還沒到四分鐘」「難道我這裡的時間跟你不一樣嗎」——根因：這兩步約定**從沒進排程帳本**，沒有錨定的
made_ts，LLM 只能拿最近一輪當「剛剛」硬算過了多久＝時間感脫離絕對時間。修：第一步存意圖＋誠實問時間；第二步補時間
→ 真入帳、到點兌現、有 made_ts 錨定就不會亂算。
"""

import os
import tempfile
import unittest
from datetime import datetime, timezone
from types import SimpleNamespace
from zoneinfo import ZoneInfo

from telegram_monitor import monitor, selfstate
from telegram_monitor.state import State

TZ = ZoneInfo("Asia/Taipei")
T0 = datetime(2026, 7, 5, 8, 19, 0, tzinfo=timezone.utc)   # 16:19 台北
T1 = datetime(2026, 7, 5, 8, 21, 0, tzinfo=timezone.utc)   # 16:21
DEFER = "你的內在感覺是怎麼來的？這個問題等一下再回答我"


class FakeClient:
    def __init__(self):
        self.sent, self.stickers, self.dry_run = [], [], False

    def send(self, text):
        self.sent.append(text)
        return True

    def send_sticker(self, file_id):
        self.stickers.append(file_id)
        return True


def _coach():
    return SimpleNamespace(
        enabled=True, api_key="k", model="m",
        meter=SimpleNamespace(record=lambda *a, **k: None),
        voice_schedule_ack=lambda q, when, h, sticker_hint="": (
            f"好，{when.strftime('%H:%M') if when else '到時候'}我會回答你剛剛問的。"),
        voice_promise_keep=lambda when, facts, h, promised="", late=False, feeling_ground="", **k: f"到點！{promised}")


def _cfg(**over):
    base = dict(dry_run=False, telegram_chat_id="", scheduled_promise_enabled=True,
                promise_emit_enabled=True, promise_sched_ttl_sec=21600, timezone="Asia/Taipei",
                notify_cooldown_min=30, promise_reply_bridge_enabled=True, promise_ledger_enabled=True,
                sched_leave_autoarm_enabled=True, deferred_promise_enabled=True)
    base.update(over)
    return SimpleNamespace(**base)


def _msg(text, when):
    return {"message": {"chat": {"id": 1}, "text": text, "date": when.timestamp()}}


_BOOM = SimpleNamespace(load_embedding_records=lambda *_: (_ for _ in ()).throw(AssertionError("延後約定不該當場答")))


def _hhmm(ts):
    return datetime.fromtimestamp(ts, timezone.utc).astimezone(TZ).strftime("%H:%M")


class DetectorTest(unittest.TestCase):
    def test_deferred_answer_request(self):
        for t in [DEFER, "等一下再回答我", "待會告訴我你的想法", "晚點回應我"]:
            self.assertTrue(selfstate.is_deferred_answer_request(t), t)

    def test_deferred_negatives(self):
        for t in ["3分鐘後回答我", "等一下我先喝水", "你不是說等一下回答我嗎", "我等一下回答你", "等等，這題好難"]:
            self.assertFalse(selfstate.is_deferred_answer_request(t), t)

    def test_time_fill(self):
        for t in ["4分鐘後", "四分鐘後", "3:50", "五分鐘", "等等3分鐘", "16:25", "5分鐘就好"]:
            self.assertTrue(selfstate.is_time_fill(t), t)

    def test_time_fill_negatives(self):
        for t in ["我5分鐘就到", "你在幹嘛", "3分鐘後回答我", "為什麼這麼久", "好啊沒問題", "明明時間超過了"]:
            self.assertFalse(selfstate.is_time_fill(t), t)

    def test_review_finding1_over_arming_guards(self):
        # 審查 HIGH：現在標記(先/能不能)／問句(嗎)／抱怨(怎麼不)／句首我／第三方受詞(告訴我媽)／第三方主語(他會告訴我) 一律不存意圖
        for t in ["等等你先回答我這題", "等一下你能不能回答我", "你等一下會回答我嗎", "你怎麼等一下都不回答我",
                  "我等一下再告訴我朋友", "我待會告訴我媽這件事", "等一下我來回覆我同事", "我晚點再回應我老闆",
                  "他等一下會告訴我答案", "我媽等一下會告訴我"]:
            self.assertFalse(selfstate.is_deferred_answer_request(t), t)
        # 合法延後請求（含「先去洗澡回來再回答我」「告訴我他會不會來」）仍存意圖
        for t in ["等一下我先去洗澡回來再回答我", "等一下告訴我他會不會來", "稍後請回覆我這封郵件"]:
            self.assertTrue(selfstate.is_deferred_answer_request(t), t)

    def test_review_finding2_natural_time_answers_fill(self):
        # 審查 MED/HIGH：問「幾分鐘後/幾點」的最自然回答（裸時距無「後」、時段鐘點）都要真的入帳（否則兩步在自己的 happy path 靜默失效）
        for t in ["五分鐘", "半小時", "5分鐘就好", "下午3點", "晚上八點", "明天早上九點", "3點半"]:
            self.assertTrue(selfstate.is_time_fill(t), t)


class E2ETest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()

    def _state(self):
        s = State(os.path.join(self.tmp, "s.json"))
        s.owner_folder_id = "F"
        return s

    def test_two_step_captures_and_fires(self):
        st = self._state()
        # 第一步：存意圖、問時間、**還沒**入帳、**不**當場答
        c1 = FakeClient()
        monitor.handle_message(_msg(DEFER, T0), _coach(), _BOOM, {"meta": {}}, None, st, c1, _cfg(), TZ)
        self.assertIsNotNone(st.pending_answer_intent)
        self.assertEqual(len(st.scheduled_promises or []), 0)
        self.assertIn("幾點", "".join(c1.sent))                          # 誠實問時間（訊息可能分段送）
        # 第二步：補「4分鐘後」→ 真入帳 target 16:25、清意圖
        c2 = FakeClient()
        monitor.handle_message(_msg("4分鐘後", T1), _coach(), _BOOM, {"meta": {}}, None, st, c2, _cfg(), TZ)
        self.assertIsNone(st.pending_answer_intent)
        pend = [p for p in (st.scheduled_promises or []) if not p.get("fulfilled")]
        self.assertEqual(len(pend), 1)
        self.assertEqual(_hhmm(pend[0]["target_ts"]), "16:25")
        self.assertEqual(_hhmm(pend[0]["made_ts"]), "16:21")           # 錨定 made_ts＝之後算「過了多久」才對得上絕對時間
        # 到點才發：16:23 不發、16:25 發
        c3 = FakeClient()
        monitor._promise_emit(c3, st, _cfg(), _coach(), datetime(2026, 7, 5, 8, 23, tzinfo=timezone.utc))
        self.assertEqual(c3.sent, [])
        c4 = FakeClient()
        monitor._promise_emit(c4, st, _cfg(), _coach(), datetime(2026, 7, 5, 8, 25, tzinfo=timezone.utc))
        self.assertTrue(c4.sent)
        self.assertTrue([p for p in st.scheduled_promises if p.get("fulfilled")])

    def test_time_fill_without_pending_does_not_capture(self):
        # 無待補意圖時，裸「4分鐘後」不憑空入帳（避免任何裸時距都被當約定）——直接測 hook（否則落一般回覆路徑需完整 fixture）
        st = self._state()
        handled = monitor._maybe_deferred_promise(FakeClient(), st, _cfg(), _coach(), "4分鐘後", T1, TZ, T1.timestamp())
        self.assertFalse(handled)
        self.assertEqual(len(st.scheduled_promises or []), 0)
        self.assertIsNone(st.pending_answer_intent)

    def test_flag_off_no_arm(self):
        st = self._state()
        handled = monitor._maybe_deferred_promise(FakeClient(), st, _cfg(deferred_promise_enabled=False),
                                                  _coach(), DEFER, T0, TZ, T0.timestamp())
        self.assertFalse(handled)                                       # 旗標關＝不接＝同現狀
        self.assertIsNone(st.pending_answer_intent)

    def test_expired_intent_not_filled_and_gc(self):
        # 意圖超過 TTL（>10分）才補時間 → 不算同一個、不入帳，且**清掉**陳舊意圖（審查 Finding 4）
        st = self._state()
        st.pending_answer_intent = {"behavior": "回答他的問題", "made_ts": T1.timestamp() - 20 * 60, "raw": DEFER}
        handled = monitor._maybe_deferred_promise(FakeClient(), st, _cfg(), _coach(), "4分鐘後", T1, TZ, T1.timestamp())
        self.assertFalse(handled)
        self.assertEqual(len([p for p in (st.scheduled_promises or []) if not p.get("fulfilled")]), 0)
        self.assertIsNone(st.pending_answer_intent)                    # 陳舊意圖被 GC

    def test_bare_duration_answer_captures(self):
        # 審查 Finding 2 端到端：問「幾分鐘後」→ 答「五分鐘」（無「後」）→ now+5 真入帳
        st = self._state()
        st.pending_answer_intent = {"behavior": "回答他的問題", "made_ts": T1.timestamp() - 60, "raw": DEFER}
        handled = monitor._maybe_deferred_promise(FakeClient(), st, _cfg(), _coach(), "五分鐘", T1, TZ, T1.timestamp())
        self.assertTrue(handled)
        pend = [p for p in (st.scheduled_promises or []) if not p.get("fulfilled")]
        self.assertEqual(len(pend), 1)
        self.assertEqual(_hhmm(pend[0]["target_ts"]), "16:26")         # 16:21+5
        self.assertIsNone(st.pending_answer_intent)


if __name__ == "__main__":
    unittest.main()
