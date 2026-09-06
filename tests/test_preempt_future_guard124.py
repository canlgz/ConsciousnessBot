"""🤝 §1.24 PREEMPT_FUTURE_GUARD＋提前誠實模式。

截圖（2026-07-12 20:59–21:05）：21:00 入帳「21:09 告訴他心情座標的變化」→ 21:04 使用者說
「等下時間到的時候」「你在告訴我你心情座標的改變」＝明顯在**確認未來約定**、不是現在要 →
bot 卻搶先誤判、21:05 發「🤝 嘿，21:09 到了，我來了」＝公然的時間謊言（此刻才 21:05）。

修（PROMISE_PREEMPT_FUTURE_GUARD，兩層分離定式：config _bool True／monitor getattr False）：
(a) 主題回報類搶先的**確定性前置守門**——使用者句含未來指涉詞（等下/待會/到時/時間到的時候/
    N分鐘後…＝_PREEMPT_FUTURE_RE ∪ selfstate._SCHED_TIMEUP_RE）→ 不送 LLM 裁決、不搶；
(b) judge prompt 追加「此刻要求 vs 確認未來約定」判準（append-only）；
(c) _promise_keep_body 內部自算 early（target−now > grace＝提前；emit/bridge 天然 False）→
    voice 注入提前誠實 note＋確定性模板 early 變體（「你先提起了，那我現在就先說」、絕不說「到了/準時」）；
(d) §0.79 同構縱深守門：early 且回覆宣稱「{when} 到了」/兌現宣稱（非引用歸屬）→ 打掉落 early 誠實模板。
叫醒/問候類 (a) 支與 _PREEMPT_TEMPLATES 一位元不碰；21:09 準時路徑逐位元同 4026c4e 基線。
全 stub、絕不打網路。
"""

import os
import tempfile
import unittest
from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import patch
from zoneinfo import ZoneInfo

from telegram_monitor import coach as coachmod
from telegram_monitor import config, gemini, monitor, persona
from telegram_monitor.state import State

TZ = ZoneInfo("Asia/Taipei")
BOOK = datetime(2026, 7, 12, 12, 59, 0, tzinfo=timezone.utc)     # 台北 7/12 20:59（聊天開場）
ASK = datetime(2026, 7, 12, 13, 4, 0, tzinfo=timezone.utc)       # 台北 7/12 21:04（質問/確認未來約定）
EMIT = datetime(2026, 7, 12, 13, 9, 0, tzinfo=timezone.utc)      # 台北 7/12 21:09（約定時刻）
TARGET = datetime(2026, 7, 12, 21, 9, 0, tzinfo=TZ).timestamp()  # 心情座標承諾 21:09 台北
MADE = "我跟你聊天一下，10 分鐘後告訴我你心情座標的變化？"

# 🤝 §1.19 既有基線④（tests/test_promise_preempt.py 釘死）：準時路徑逐字不變的對照。
WAKE_BOOK = datetime(2026, 7, 11, 15, 11, 0, tzinfo=timezone.utc)   # 台北 7/11 23:11
WAKE_EMIT = datetime(2026, 7, 11, 23, 0, 0, tzinfo=timezone.utc)    # 台北 7/12 07:00
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
    """reader stub：任何屬性都回「回空列表的函式」（照 tests/test_promise_preempt.py 樣式）。"""

    def __getattr__(self, name):
        return lambda *a, **k: []


def _coach(judge=None, judge_raises=False, keep_voice=None):
    """假教練（零網路）：judge_promise_preempt 可控＋計數；voice_promise_keep 記錄 kwargs（驗 early 透傳）。"""
    c = SimpleNamespace(enabled=True, api_key="k", model="m",
                        meter=SimpleNamespace(record=lambda *a, **k: None),
                        judge_calls=[], keep_kwargs=[])

    def _judge(user_text, promise_text):
        c.judge_calls.append((user_text, promise_text))
        if judge_raises:
            raise gemini.GeminiError("網路炸了")
        return judge(user_text) if judge else None

    def _keep(*a, **k):
        c.keep_kwargs.append(dict(k))
        return keep_voice

    c.reply = lambda *a, **k: "嗯。"
    c.ask = lambda *a, **k: ("chat", None, "嗯。")
    c.voice_greeting = lambda *a, **k: "嗨。"
    c.voice_schedule_ack = lambda q, when, h, sticker_hint="": (
        f"好，{when.strftime('%H:%M') if when else '到時候'}我會來。")
    c.voice_promise_ack = lambda q, h: "好。"
    c.voice_promise_keep = _keep
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
                promise_preempt_enabled=True, promise_preempt_window_sec=5400,
                promise_preempt_future_guard_enabled=True)
    base.update(over)
    return SimpleNamespace(**base)


def _cfg_no_guard(**over):
    """旗標消融用：假 cfg **無** promise_preempt_future_guard_enabled 屬性＝getattr 預設 False＝基線。"""
    c = _cfg(**over)
    delattr(c, "promise_preempt_future_guard_enabled")
    return c


def _msg(text, when):
    return {"message": {"chat": {"id": 1}, "text": text, "date": when.timestamp()}}


def _mood_promise(target=TARGET):
    """截圖那筆：主題回報類（behavior 非 _PREEMPT_TEMPLATES 標籤）→ 走 (b) 支。"""
    return {"target_ts": target, "action": MADE[:120], "made_ts": BOOK.timestamp(),
            "made_text": MADE[:200], "fulfilled": False,
            "behavior": "告訴他心情座標的變化", "status": "pending"}


_TURN_KEYS = ("self_promise_ctx", "self_promise_skip", "promise_guard_llm_no",
              "keep_claim_ground", "ground_now", "last_reply")


class _Base(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        monitor._TURN["bubbles"] = None
        for k in _TURN_KEYS:
            monitor._TURN.pop(k, None)

    def tearDown(self):
        monitor._TURN["bubbles"] = None
        for k in _TURN_KEYS:
            monitor._TURN.pop(k, None)

    def _state(self):
        s = State(os.path.join(self.tmp, f"s{id(self)}.json"))
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


class FutureGuardTest(_Base):
    """(a) 確定性前置未來詞守門：在講未來約定＝不送 LLM、不搶。"""

    def test_t1_future_words_block_before_judge(self):
        """T1（截圖重演 A①）：帳上 21:09 心情座標承諾、21:04 收「等下時間到的時候」→ 不搶
        （promise 仍 pending、無 🤝 訊息）、judge **零呼叫**（呼叫即 raise 的 stub 證明守門在 judge 前）。"""
        def _boom(t):
            raise RuntimeError("§1.24 未來詞守門失效：judge 不該被呼叫")

        for text in ("等下時間到的時候", "待會時間到的時候你再跟我說", "10分鐘後再告訴我"):
            st = self._state()
            st.scheduled_promises = [_mood_promise()]
            cl, co = self._turn(st, text, ASK, judge=_boom)
            self.assertEqual(self._handshakes(cl), [], text)                # 不搶＝無 🤝
            self.assertEqual(len(co.judge_calls), 0, text)                  # 零 LLM 呼叫
            p = st.scheduled_promises[0]
            self.assertFalse(p.get("fulfilled"), text)                      # 仍 pending＝到點照常兌現
            self.assertNotIn("preempted", p, text)

    def test_t1b_merged_bubbles_no_judge_either(self):
        """T1b：兩顆泡泡被連發合併成一句（截圖原文）→ 同樣零 judge、不搶（守門或更早的 fast-path 接手皆可，
        但**絕不**搶先兌現）。"""
        def _boom(t):
            raise RuntimeError("§1.24：judge 不該被呼叫")

        st = self._state()
        st.scheduled_promises = [_mood_promise()]
        cl, co = self._turn(st, "等下時間到的時候，你在告訴我你心情座標的改變", ASK, judge=_boom)
        self.assertEqual(self._handshakes(cl), [])
        self.assertEqual(len(co.judge_calls), 0)
        self.assertFalse(st.scheduled_promises[0].get("fulfilled"))

    def test_t5_flag_off_judge_still_called(self):
        """T5（消融）：假 cfg **無**旗標屬性 → 21:04 未來詞句照舊送 judge（＝§1.19 現狀）、early 不傳。"""
        st = self._state()
        st.scheduled_promises = [_mood_promise()]
        cl, co = self._turn(st, "等下時間到的時候", ASK, judge=lambda t: False, cfg=_cfg_no_guard())
        self.assertEqual(len(co.judge_calls), 1)                            # 照舊送 judge＝同現狀
        self.assertEqual(self._handshakes(cl), [])                          # judge 否 → 不搶

    def test_t5b_flag_off_preempt_keeps_no_early_kwarg(self):
        """T5b（消融）：旗標關、judge True → 搶先照舊發生，voice 不帶 early kwarg＝既有嚴格簽名假教練零破壞。"""
        st = self._state()
        st.scheduled_promises = [_mood_promise()]
        cl, co = self._turn(st, "你現在就跟我說心情座標吧", ASK, judge=lambda t: True,
                            keep_voice="我心情座標有些下沉。", cfg=_cfg_no_guard())
        self.assertEqual(len(self._handshakes(cl)), 1)
        self.assertTrue(co.keep_kwargs)
        self.assertNotIn("early", co.keep_kwargs[-1])                       # early 只在旗標開才傳


class EarlyHonestyTest(_Base):
    """(c) 提前誠實模式：真要提前兌現時，措辭誠實、絕不說「到了/準時」。"""

    def test_t2_preempt_now_request_honest_template(self):
        """T2（截圖重演 A②）：句子無未來詞「你現在就跟我說心情座標吧」＋judge True → 搶先兌現發生，
        但模板退路走 early 變體：不說「到了/準時」、含「先」語意＋正確的 21:09。"""
        st = self._state()
        st.scheduled_promises = [_mood_promise()]
        cl, co = self._turn(st, "你現在就跟我說心情座標吧", ASK, judge=lambda t: True, keep_voice=None)
        hs = self._handshakes(cl)
        self.assertEqual(len(hs), 1)
        msg = hs[0]
        self.assertEqual(len(co.judge_calls), 1)                            # 無未來詞＝照送 judge
        self.assertNotIn("到了", msg)                                       # 絕不謊稱「21:09 到了」
        self.assertNotIn("準時", msg)
        self.assertTrue(("先" in msg) or ("提前" in msg), msg)              # 提前誠實語意
        self.assertIn("還沒到21:09", msg)                                   # 時刻程式算、誠實說還沒到
        p = st.scheduled_promises[0]
        self.assertTrue(p.get("fulfilled"))
        self.assertTrue(p.get("preempted"))

    def test_t2b_voice_gets_early_kwarg(self):
        """T2b：voice 路徑收到 early=True 透傳（假教練記錄 kwargs）。"""
        st = self._state()
        st.scheduled_promises = [_mood_promise()]
        cl, co = self._turn(st, "你現在就跟我說心情座標吧", ASK, judge=lambda t: True,
                            keep_voice="你先提起了，那我現在就先說——我有些下沉。")
        self.assertEqual(len(self._handshakes(cl)), 1)
        self.assertTrue(co.keep_kwargs)
        self.assertIs(co.keep_kwargs[-1].get("early"), True)

    def test_persona_early_note_replaces_arrival_claim(self):
        """persona.promise_keep_user(early=True)：prompt 注入提前誠實 note（還沒到 {when}、你先提起了、
        絕對不要說「{when} 到了」「我準時來了」）；early=False＝逐位元同現狀（仍是「{when} 到了」指涉約定的準確時刻守則）。"""
        u = persona.promise_keep_user("21:09", "〔此刻真的是 21:05、晚上〕",
                                      promised="告訴他心情座標的變化", early=True)
        self.assertIn("還沒到21:09", u)
        self.assertIn("你先提起了", u)
        self.assertIn("絕對不要說「21:09 到了」", u)
        self.assertIn("我準時來了", u)
        u0 = persona.promise_keep_user("21:09", "〔此刻真的是 21:09、晚上〕",
                                       promised="告訴他心情座標的變化")
        self.assertIn("「21:09 到了」", u0)                                 # 既有準確時刻守則原樣（early 預設 False）
        self.assertNotIn("你先提起了", u0)
        # 打招呼支（promised 空）同步帶 note
        ug = persona.promise_keep_user("21:09", "", early=True)
        self.assertIn("你先提起了", ug)

    def test_coach_passes_early_through(self):
        """coach.voice_promise_keep(early=True) 透傳到 persona（stub gemini 攔 prompt）。"""
        captured = {}

        def _fake_chat(api_key, model, system, contents, **kw):
            captured["prompt"] = contents[-1]["parts"][0]["text"]
            return "你先提起了，那我現在就先說。"

        co = coachmod.Coach(SimpleNamespace(gemini_api_key="k"))
        with patch("telegram_monitor.gemini.generate_chat", _fake_chat):
            out = co.voice_promise_keep("21:09", "", None, promised="告訴他心情座標的變化", early=True)
        self.assertTrue(out)
        self.assertIn("還沒到21:09", captured["prompt"])
        self.assertIn("絕對不要說「21:09 到了」", captured["prompt"])


class DepthGuardTest(_Base):
    """(d) §0.79 同構縱深守門：early 卻宣稱到點＝就地打掉、落 early 誠實模板。"""

    def test_t3_voice_time_lie_rewritten(self):
        """T3（截圖破口④）：stub voice 回「嘿，21:09 到了，我來了」→ 出口被打掉、落 early 誠實模板。"""
        st = self._state()
        st.scheduled_promises = [_mood_promise()]
        cl, _ = self._turn(st, "你現在就跟我說心情座標吧", ASK, judge=lambda t: True,
                           keep_voice="嘿，21:09 到了，我來了。")
        hs = self._handshakes(cl)
        self.assertEqual(len(hs), 1)
        self.assertNotIn("我來了", hs[0])                                   # 謊言泡泡整則不出
        self.assertNotIn("21:09 到了", hs[0])
        self.assertIn("還沒到21:09", hs[0])                                 # 落 early 誠實模板
        self.assertIn("先", hs[0])

    def test_t3b_quoted_attribution_not_rewritten(self):
        """T3b：「你說21:09到了」＝引用歸屬（複述對方）→ 不打（比照 §0.82 _NOW_QUOTE_RE 精神）。"""
        st = self._state()
        st.scheduled_promises = [_mood_promise()]
        quoted = "你說21:09到了？其實還沒——你先提起了，那我現在就先說。"
        cl, _ = self._turn(st, "你現在就跟我說心情座標吧", ASK, judge=lambda t: True, keep_voice=quoted)
        hs = self._handshakes(cl)
        self.assertEqual(len(hs), 1)
        self.assertIn("你說21:09到了", hs[0])                               # 引用保留、不誤殺


class OnTimeBitwiseTest(_Base):
    """④ 準時路徑零位元變動（旗標開也一樣：emit 到達時 target≤now＝early 天然 False）。"""

    def test_t4_on_time_emit_baseline_verbatim(self):
        st = self._state()
        self._turn(st, "明天早上七點叫我起床喔", WAKE_BOOK)                 # 真捕捉路徑入帳
        cl = FakeClient()
        monitor._promise_emit(cl, st, _cfg(), _coach(), WAKE_EMIT)          # 旗標開、07:00 到點
        self.assertEqual(cl.sent, [BASELINE_EMIT])                          # 逐字＝4026c4e 基線④
        self.assertEqual(st.scheduled_promises[0].get("status"), "fulfilled")

    def test_t4b_mood_promise_on_time_emit_no_early_wording(self):
        """21:09 準時到點：即使旗標開，兌現句照舊模板、無「還沒到/先提起」字樣。"""
        st = self._state()
        st.scheduled_promises = [_mood_promise()]
        cl = FakeClient()
        monitor._promise_emit(cl, st, _cfg(), _coach(), EMIT)
        self.assertEqual(len(cl.sent), 1)
        self.assertNotIn("還沒到", cl.sent[0])
        self.assertNotIn("先提起", cl.sent[0])


class PromptAndAckGuardrailTest(unittest.TestCase):
    """(b) judge prompt 追加判準（append-only）＋ ⑥ schedule_ack 的 _exact_time_clause 未動護欄。"""

    def test_judge_prompt_appended_future_criterion(self):
        g = persona.PROMISE_PREEMPT_JUDGE_SYSTEM
        self.assertIn("此刻", g)
        self.assertIn("未來的約定", g)
        self.assertIn("等下", g)
        # 既有字一個不刪（test_promise_preempt.py 的斷言同步在此重申）
        self.assertIn("嚴禁", g)
        self.assertIn("時間由系統時鐘管理", g)
        self.assertIn("只判斷", g)

    def test_schedule_ack_exact_clause_untouched(self):
        """⑥ persona._exact_time_clause 本體不動：schedule_ack_user 輸出不變（無提前誠實字樣）。"""
        a = persona.schedule_ack_user("八點跟我打招呼", "08:00")
        self.assertIn("說好 08:00 的", a)
        self.assertIn("「08:00 到了」", a)                                   # 原守則字樣仍在
        self.assertNotIn("你先提起了", a)
        self.assertNotIn("提前", a)


class ConfigSyncTest(unittest.TestCase):
    def test_flag_default_true(self):
        os.environ.pop("PROMISE_PREEMPT_FUTURE_GUARD", None)
        cfg = config.Config.load()
        self.assertIs(cfg.promise_preempt_future_guard_enabled, True)       # 真部署預設恆生效

    def test_flag_env_off(self):
        with patch.dict(os.environ, {"PROMISE_PREEMPT_FUTURE_GUARD": "0"}):
            cfg = config.Config.load()
            self.assertIs(cfg.promise_preempt_future_guard_enabled, False)


if __name__ == "__main__":
    unittest.main()
