"""🤝 §1.19 PROMISE_PREEMPT_LINK：搶先兌現的因果連結（未來窗掃描＋🤝 泡泡＋recur 顯式推進）。

截圖（2026-07-12）：7/11 23:11 使用者約「明天早上七點叫我起床喔」→ 入帳 7/12 07:00。7/12 06:55 使用者
**刻意提前**說「早安」→ bot 只回「早安！你今天起得真早啊。」——完全沒連結到 07:00 的叫醒約定；07:00 照發
「說好 07:00 要來叫你起床」＝對明明已醒著聊過天的人裝叫醒；被問「我不是跟你說過了？」還否認。

設計（handle_message 內、「同時…」併入塊之後、各 route dispatch 之前；一輪最多處理一筆、**不 return**）：
掃 pending 且 **now < target ≤ now+window**（預設 90 分）的筆——
(a) 叫醒/問候類（behavior ∈ {叫他起床, 跟他打招呼, 問候他}）：**任何**使用者訊息＝人已醒著/在場 → 先送
    因果 🤝 泡泡（確定性模板、HH:MM 由程式算，不走 LLM），照常往下回這句；
(b) 主題回報類（behavior=='' 或非上述標籤）：使用者這句真在問那個主題才搶——LLM 逃生閘
    judge_promise_preempt（只回是/否、**協定無時間欄位**；失敗＝否＝不搶＝到點照常兌現）。
標記：單次筆 fulfilled/status='fulfilled'/fulfilled_ts＋preempted=True（審計留痕；status 禁用新字串）；
recur=daily 筆**不標 fulfilled**、顯式 target_ts += 86400＋last_fired_ts（標 fulfilled 會殺死每天約定；
_promise_mark_kept 的 while 對未來 target 不推進＝當天照發，皆為實測陷阱）。
到點側 _promise_emit 一行不動：搶先筆已 fulfilled/已推進＝自然跳過；無人搶先＝準時路徑逐位元同現狀。
嚴格未來窗＝逾期 pending（bridge 管）零觸碰。旗標 PROMISE_PREEMPT_LINK=0＝不掃＝同現狀。
全 stub、絕不打網路。
"""

import os
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import patch
from zoneinfo import ZoneInfo

from telegram_monitor import config, gemini, monitor, persona
from telegram_monitor.state import State

TZ = ZoneInfo("Asia/Taipei")
BOOK = datetime(2026, 7, 11, 15, 11, 0, tzinfo=timezone.utc)    # 台北 7/11 23:11（截圖：約定叫醒）
PRE = datetime(2026, 7, 11, 22, 55, 0, tzinfo=timezone.utc)     # 台北 7/12 06:55（刻意提前說早安）
EMIT = datetime(2026, 7, 11, 23, 0, 0, tzinfo=timezone.utc)     # 台北 7/12 07:00（約定時刻）
TARGET = datetime(2026, 7, 12, 7, 0, 0, tzinfo=TZ).timestamp()  # 叫醒約定 7/12 07:00 台北

TOPIC_TARGET = datetime(2026, 7, 11, 11, 0, 0, tzinfo=TZ).timestamp()   # 讀經回報約 7/11 11:00 台北
TOPIC_ASK = datetime(2026, 7, 11, 2, 20, 0, tzinfo=timezone.utc)        # 台北 7/11 10:20（提前問進度）
TOPIC_EMIT = datetime(2026, 7, 11, 3, 0, 0, tzinfo=timezone.utc)        # 台北 7/11 11:00
TOPIC_TEXT = "明天早上 11 點，我會過來這裡，跟你說說我看到你的『讀誦經書』有沒有新的進展。"

# 🤝 4026c4e 基線④（主管實錄）：無人搶先時 07:00 準時兌現的**逐字**輸出——T2 位元一致對照組。
BASELINE_EMIT = "🤝 我說過07:00要為你做一件事——叫他起床。"


class FakeClient:
    def __init__(self):
        self.sent, self.stickers, self.dry_run = [], [], False

    def send(self, text):
        self.sent.append(text)
        return True

    def send_sticker(self, file_id):
        self.stickers.append(file_id)
        return True


class _Benign:
    """reader stub：任何屬性都回「回空列表的函式」（照 tests/test_bot_self_promise.py 樣式）。"""

    def __getattr__(self, name):
        return lambda *a, **k: []


def _coach(judge=None, judge_raises=False, keep_voice=None):
    """假教練（零網路）：greeting/reply 都有；judge_promise_preempt 可控＋計數；voice_promise_keep 預設 None＝
    _promise_keep_body 走確定性模板（T2 逐字基線的前提）。"""
    c = SimpleNamespace(enabled=True, api_key="k", model="m",
                        meter=SimpleNamespace(record=lambda *a, **k: None),
                        judge_calls=[])

    def _judge(user_text, promise_text):
        c.judge_calls.append((user_text, promise_text))
        if judge_raises:
            raise gemini.GeminiError("網路炸了")
        return judge(user_text) if judge else None

    c.reply = lambda *a, **k: "嗯。"
    c.ask = lambda *a, **k: ("chat", None, "嗯。")
    c.voice_greeting = lambda *a, **k: "早安呀，你起得真早。"
    c.voice_schedule_ack = lambda q, when, h, sticker_hint="": (
        f"好，{when.strftime('%H:%M') if when else '到時候'}我會來。")
    c.voice_promise_ack = lambda q, h: "好。"
    c.voice_promise_keep = (lambda *a, **k: keep_voice)
    c.judge_timed_request = lambda t: None
    c.judge_self_promise = lambda t: None
    c.judge_promise_preempt = _judge
    return c


def _cfg(**over):
    base = dict(dry_run=False, telegram_chat_id="", scheduled_promise_enabled=True,
                promise_emit_enabled=True, promise_sched_ttl_sec=21600, timezone="Asia/Taipei",
                notify_cooldown_min=30, promise_reply_bridge_enabled=False, promise_ledger_enabled=True,
                sched_leave_autoarm_enabled=True, deferred_promise_enabled=True,
                promise_llm_rescue_enabled=True, bot_self_promise_enabled=False,
                promise_preempt_enabled=True, promise_preempt_window_sec=5400)
    base.update(over)
    return SimpleNamespace(**base)


def _msg(text, when):
    return {"message": {"chat": {"id": 1}, "text": text, "date": when.timestamp()}}


def _wake(recur=None, target=TARGET):
    p = {"target_ts": target, "action": "明天早上七點叫我起床喔", "made_ts": BOOK.timestamp(),
         "made_text": "明天早上七點叫我起床喔", "fulfilled": False,
         "behavior": "叫他起床", "status": "pending"}
    if recur:
        p["recur"] = recur
    return p


_TURN_KEYS = ("self_promise_ctx", "self_promise_skip", "promise_guard_llm_no",
              "keep_claim_ground", "ground_now", "last_reply")


class PromisePreemptE2ETest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        monitor._TURN["bubbles"] = None
        for k in _TURN_KEYS:
            monitor._TURN.pop(k, None)   # 隔離跨測試殘留

    def tearDown(self):
        monitor._TURN["bubbles"] = None
        for k in _TURN_KEYS:
            monitor._TURN.pop(k, None)

    def _state(self):
        s = State(os.path.join(self.tmp, "s.json"))
        s.owner_folder_id = "F"
        return s

    def _turn(self, st, text, when, judge=None, judge_raises=False, keep_voice=None, cfg=None):
        cl = FakeClient()
        co = _coach(judge=judge, judge_raises=judge_raises, keep_voice=keep_voice)
        with patch("telegram_monitor.coach.build_memory_brief", return_value="brief"):
            monitor.handle_message(_msg(text, when), co, _Benign(), {"meta": {}}, None, st, cl,
                                   cfg or _cfg(), TZ)
        return cl, co

    def _handshakes(self, cl):
        return [m for m in cl.sent if m.startswith("🤝")]

    # ---- T1：截圖重演（叫醒類、單次筆） ----

    def test_t1_early_good_morning_preempts_wake_call(self):
        """T1：23:11 真捕捉路徑入帳（behavior='叫他起床'、target 7/12 07:00）→ 06:55「早安」→
        一顆 🤝 因果泡泡（含 07:00＋叫你起床）＋標 preempted/fulfilled；07:00 emit 靜默；早安照樣得到問候。"""
        st = self._state()
        self._turn(st, "明天早上七點叫我起床喔", BOOK)                      # 使用者請求路徑（好的，別碰）
        proms = st.scheduled_promises or []
        self.assertEqual(len(proms), 1)
        self.assertEqual(proms[0].get("behavior"), "叫他起床")
        self.assertEqual(round(proms[0]["target_ts"]), round(TARGET))
        self.assertFalse(proms[0].get("fulfilled"))

        cl, co = self._turn(st, "早安", PRE)
        hs = self._handshakes(cl)
        self.assertEqual(len(hs), 1)                                        # 恰一顆因果 🤝 泡泡
        self.assertIn("07:00", hs[0])                                       # HH:MM 由程式算（非 LLM）
        self.assertIn("叫你起床", hs[0])                                    # 因果連結＋他→你人稱
        self.assertTrue(cl.sent[0].startswith("🤝"))                        # 先講因果、再回這句
        self.assertIn("早安呀", "".join(cl.sent))                           # 不 return：早安照樣得到問候回覆
        p = st.scheduled_promises[0]
        self.assertTrue(p.get("preempted"))
        self.assertTrue(p.get("fulfilled"))
        self.assertEqual(p.get("status"), "fulfilled")
        self.assertEqual(round(p.get("fulfilled_ts")), round(PRE.timestamp()))
        self.assertEqual(len(co.judge_calls), 0)                            # 叫醒類＝確定性、不燒 LLM

        cl2 = FakeClient()
        monitor._promise_emit(cl2, st, _cfg(), _coach(), EMIT)              # 07:00 到點
        self.assertEqual(cl2.sent, [])                                      # 不對醒著的人裝叫醒

    # ---- T2：對照組（無人搶先＝準時路徑位元一致） ----

    def test_t2_no_preempt_control_emit_bitwise_baseline(self):
        """T2：同帳、06:55 無任何使用者訊息 → 07:00 emit 輸出**逐字等於** 4026c4e 基線④。"""
        st = self._state()
        self._turn(st, "明天早上七點叫我起床喔", BOOK)
        cl = FakeClient()
        monitor._promise_emit(cl, st, _cfg(), _coach(), EMIT)
        self.assertEqual(cl.sent, [BASELINE_EMIT])
        p = st.scheduled_promises[0]
        self.assertEqual(p.get("status"), "fulfilled")
        self.assertNotIn("preempted", p)                                    # 準時路徑帳本零新鍵

    # ---- T3：recur=daily 顯式推進（陷阱 A/B） ----

    def test_t3_daily_recur_advances_instead_of_fulfilled(self):
        """T3：每天叫醒筆被 06:55 早安搶先 → 不標 fulfilled、target 恰 +86400、last_fired_ts=06:55；
        隔天 07:00 emit 照發（約定不死）。"""
        st = self._state()
        st.scheduled_promises = [_wake(recur="daily")]
        cl, _ = self._turn(st, "早安", PRE)
        self.assertEqual(len(self._handshakes(cl)), 1)
        p = st.scheduled_promises[0]
        self.assertFalse(p.get("fulfilled"))                                # 陷阱 A：標 fulfilled 會殺死每天約定
        self.assertEqual(round(p["target_ts"]), round(TARGET + 86400))      # 陷阱 B：顯式 +86400（while 不推未來 target）
        self.assertEqual(round(p.get("last_fired_ts")), round(PRE.timestamp()))
        self.assertTrue(p.get("preempted"))

        cl2 = FakeClient()
        monitor._promise_emit(cl2, st, _cfg(), _coach(), EMIT + timedelta(days=1))
        self.assertEqual(len(self._handshakes(cl2)), 1)                     # 隔天 07:00 照發

    # ---- T4：主題回報類（LLM 逃生閘） ----

    def _topic_state(self):
        st = self._state()
        st.scheduled_promises = [{"target_ts": TOPIC_TARGET, "action": TOPIC_TEXT[:300],
                                  "made_ts": BOOK.timestamp() - 86400, "made_text": TOPIC_TEXT[:300],
                                  "fulfilled": False, "behavior": "", "status": "pending", "origin": "self"}]
        return st

    def test_t4_topic_ask_judge_yes_fulfills_now(self):
        """T4a：behavior='' 讀經回報筆、10:20 問「讀誦經書的進度怎樣？」judge True → 當場 🤝 兌現＋標
        fulfilled/preempted；11:00 emit 靜默。"""
        st = self._topic_state()
        cl, co = self._turn(st, "讀誦經書的進度怎樣？", TOPIC_ASK, judge=lambda t: True,
                            keep_voice="我來跟你說讀誦經書的進展——我看到你有新的進度。")
        hs = self._handshakes(cl)
        self.assertEqual(len(hs), 1)
        self.assertIn("讀誦經書", hs[0])                                    # 兌現句走 _promise_keep_body 同鏈
        self.assertEqual(len(co.judge_calls), 1)
        self.assertIn("讀誦經書", co.judge_calls[0][1])                     # 閘拿到 made_text 當比對脈絡
        p = st.scheduled_promises[0]
        self.assertTrue(p.get("fulfilled"))
        self.assertTrue(p.get("preempted"))

        cl2 = FakeClient()
        monitor._promise_emit(cl2, st, _cfg(), _coach(), TOPIC_EMIT)
        self.assertEqual(cl2.sent, [])

    def test_t4_topic_judge_no_or_error_keeps_target(self):
        """T4b：judge False／raise GeminiError → 不搶（失敗安全）、帳不動；11:00 emit 照常兌現。"""
        for kw in (dict(judge=lambda t: False), dict(judge_raises=True)):
            st = self._topic_state()
            for k in _TURN_KEYS:
                monitor._TURN.pop(k, None)
            cl, _ = self._turn(st, "讀誦經書的進度怎樣？", TOPIC_ASK, **kw)
            self.assertEqual(self._handshakes(cl), [], kw)
            p = st.scheduled_promises[0]
            self.assertFalse(p.get("fulfilled"), kw)
            self.assertNotIn("preempted", p, kw)
            cl2 = FakeClient()
            monitor._promise_emit(cl2, st, _cfg(), _coach(), TOPIC_EMIT)
            self.assertEqual(len(self._handshakes(cl2)), 1, kw)             # 到點照常兌現

    # ---- T5：超窗不早搶 ----

    def test_t5_beyond_window_untouched(self):
        """T5：target=now+3h（> 90 分窗）＋「早安」→ 不搶、帳不動。"""
        st = self._state()
        st.scheduled_promises = [_wake(target=PRE.timestamp() + 3 * 3600)]
        cl, _ = self._turn(st, "早安", PRE)
        self.assertEqual(self._handshakes(cl), [])
        p = st.scheduled_promises[0]
        self.assertFalse(p.get("fulfilled"))
        self.assertNotIn("preempted", p)

    # ---- T6：逾期 pending 零觸碰（bridge 的地盤；e2e 場景 C 對齊） ----

    def test_t6_overdue_pending_zero_touch(self):
        """T6：target < now 的逾期筆＋使用者訊息（bridge 關）→ 本機制嚴格未來窗＝零觸碰。"""
        st = self._state()
        st.scheduled_promises = [_wake(target=PRE.timestamp() - 600)]
        cl, _ = self._turn(st, "早安", PRE)
        self.assertEqual(self._handshakes(cl), [])
        p = st.scheduled_promises[0]
        self.assertFalse(p.get("fulfilled"))
        self.assertNotIn("preempted", p)
        self.assertEqual(round(p["target_ts"]), round(PRE.timestamp() - 600))

    # ---- T7：承諾管理輪語境排除 ----

    def test_t7_scheduled_and_ledger_routes_excluded(self):
        """T7：「21:47跟我打招呼」（route=scheduled_promise）與「你有叫我嗎」（didcall→promise_ledger）
        → 皆不觸發搶先（本句自己在約新約/在對帳）。"""
        st = self._state()
        st.scheduled_promises = [_wake()]
        cl, co = self._turn(st, "21:47跟我打招呼", PRE)                     # 約新約：正常入帳 ack、不搶
        self.assertEqual(self._handshakes(cl), [])
        self.assertNotIn("preempted", st.scheduled_promises[0])
        self.assertEqual(len(st.scheduled_promises), 2)                     # 新約真的入帳（既有路徑不破）

        st2 = State(os.path.join(self.tmp, "s2.json"))
        st2.owner_folder_id = "F"
        st2.scheduled_promises = [_wake()]
        cl2, _ = self._turn(st2, "你有叫我嗎", PRE)                         # 對帳輪：ledger 誠實答、不搶
        self.assertEqual(self._handshakes(cl2), [])
        p = st2.scheduled_promises[0]
        self.assertFalse(p.get("fulfilled"))
        self.assertNotIn("preempted", p)

    # ---- T8：旗標消融（0＝逐位元同現狀） ----

    def test_t8_flag_off_same_as_status_quo(self):
        """T8：PROMISE_PREEMPT_LINK=0 → T1 同輸入：無 🤝 泡泡、帳不動、07:00 照發基線句＝同現狀。"""
        st = self._state()
        self._turn(st, "明天早上七點叫我起床喔", BOOK, cfg=_cfg(promise_preempt_enabled=False))
        cl, co = self._turn(st, "早安", PRE, cfg=_cfg(promise_preempt_enabled=False))
        self.assertEqual(self._handshakes(cl), [])
        p = st.scheduled_promises[0]
        self.assertFalse(p.get("fulfilled"))
        self.assertNotIn("preempted", p)
        self.assertEqual(len(co.judge_calls), 0)
        cl2 = FakeClient()
        monitor._promise_emit(cl2, st, _cfg(promise_preempt_enabled=False), _coach(), EMIT)
        self.assertEqual(cl2.sent, [BASELINE_EMIT])                         # 07:00 照發＝同現狀


class JudgeProtocolTest(unittest.TestCase):
    """persona 協定：只回是/否、沒有時間欄位＝LLM 無從回傳時刻（§1.12 鐵律）。"""

    def test_system_prompt_forbids_time(self):
        g = persona.PROMISE_PREEMPT_JUDGE_SYSTEM
        self.assertIn("嚴禁", g)
        self.assertIn("時間由系統時鐘管理", g)
        self.assertIn("是", g)
        self.assertIn("否", g)

    def test_user_prompt_contains_both_texts(self):
        u = persona.promise_preempt_judge_user("讀誦經書的進度怎樣？", TOPIC_TEXT)
        self.assertIn("讀誦經書的進度怎樣？", u)
        self.assertIn("明天早上 11 點", u)


class ConfigSyncTest(unittest.TestCase):
    def test_flag_and_window_exist(self):
        os.environ.pop("PROMISE_PREEMPT_LINK", None)
        os.environ.pop("PROMISE_PREEMPT_WINDOW_SEC", None)
        cfg = config.Config.load()
        self.assertIsInstance(cfg.promise_preempt_enabled, bool)
        self.assertEqual(cfg.promise_preempt_window_sec, 5400)

    def test_flag_off_and_window_env(self):
        with patch.dict(os.environ, {"PROMISE_PREEMPT_LINK": "0", "PROMISE_PREEMPT_WINDOW_SEC": "600"}):
            cfg = config.Config.load()
            self.assertFalse(cfg.promise_preempt_enabled)
            self.assertEqual(cfg.promise_preempt_window_sec, 600)


if __name__ == "__main__":
    unittest.main()
