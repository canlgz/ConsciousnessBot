"""🤖 §1.18 BOT_SELF_PROMISE：bot 自己開口的承諾也入帳（自發承諾是一等公民）。

截圖（2026-07-10 21:50）：bot 自己說「明天早上 11 點，我會過來這裡，跟你說說我看到你的『讀誦經書』
有沒有新的進展。」——這句從未入帳。隔天 11:06 整段全靠 LLM 對話史即興（時態還在未來、日期幻覺 7/12、
過期無感）；7/12 07:01 更直接否認「沒聽到你說」。根因：全 repo 不存在任何「bot 出訊承諾捕捉」機制。

設計（掛在 _say 互動出口、掃**最終真送出**的文字）：
(a) 確定性結構網 `我(?:會|來|過來)` → 不中零成本跳出；
(b) temporal.all_clock_epochs(**bot 那句話**) 取未來錨 → 空跳出（「我會努力…」自然濾掉）；
(c) 時刻鄰近去重（round-only ±60s，對帳上未兌現筆）→ 擋 ack 殘餘與同刻重述；
(d) LLM 逃生閘 judge_self_promise **必經**：判「正在立下新的」vs「複誦既有/對帳/認錯/提案徵詢」——
    複誦「我會記得在明天早上十一點…」會被 temporal 滾成隔天的未來錨，時刻過濾救不了、只有語意判得出；
    **§1.12 鐵律：協定無時間欄位、動作經 _sanitize_llm_action 剝時刻、時間永遠 temporal 解 bot 那句話**；
    GeminiError／解析失敗／coach 停用 → 失敗安全＝不入帳；
(e) _book_self_promise 直接 append（origin='self'、不走 _book_scheduled_targets＝不在 _say 內遞迴 ack）。
入帳 ack（「12:18我會來…」）由 _TURN['self_promise_skip'] 顯式排除；帳本盤點/承諾管理輪
（route.kind ∈ {promise_ledger, scheduled_promise}）語境排除。旗標 BOT_SELF_PROMISE=0＝逐位元同現狀。
全 stub、絕不打網路。
"""

import os
import tempfile
import unittest
from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import patch
from zoneinfo import ZoneInfo

from telegram_monitor import config, gemini, monitor, persona
from telegram_monitor.state import State

TZ = ZoneInfo("Asia/Taipei")
NOW = datetime(2026, 7, 10, 13, 50, 0, tzinfo=timezone.utc)      # 台北 21:50（截圖 bot 開口承諾時刻）
NOW_MORNING = datetime(2026, 7, 11, 2, 0, 0, tzinfo=timezone.utc)   # 台北 7/11 10:00（T3 用，12:18 在未來）
NOW_ASK = datetime(2026, 7, 11, 3, 6, 0, tzinfo=timezone.utc)    # 台北 7/11 11:06（截圖問「還有什麼承諾」時刻）
TARGET = datetime(2026, 7, 11, 11, 0, 0, tzinfo=TZ).timestamp()  # 約定時刻 7/11 11:00 台北

BOT_PROMISE = "明天早上 11 點，我會過來這裡，跟你說說我看到你的『讀誦經書』有沒有新的進展。"
BOT_RECITAL = "我會記得在明天早上十一點跟你說讀誦經書的進展。"   # 11:06 的複誦（temporal 會滾成 7/12 11:00 未來錨）
BOT_PROPOSAL = "明天早上 11 點我過來，這樣可以嗎？"              # 提案徵詢：結構網會中、須 LLM 閘擋
BOT_EFFORT = "我會努力變得更好。"                                # 無時間錨：temporal 層就濾掉、不燒 LLM


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
    """reader stub：任何屬性都回「回空列表的函式」（照 tests/test_global_time_governance82.py 樣式）。"""

    def __getattr__(self, name):
        return lambda *a, **k: []


def _coach(voice="嗯。", judge=None, judge_raises=False):
    """假教練（零網路）：reply/ask 都回 voice（互動回覆＝bot 說出口的那句）；judge_self_promise 可控＋計數。"""
    c = SimpleNamespace(enabled=True, api_key="k", model="m",
                        meter=SimpleNamespace(record=lambda *a, **k: None),
                        judge_calls=[], acks=[])

    def _sched_ack(q, when, h, sticker_hint=""):
        c.acks.append(when)
        return f"好，{when.strftime('%H:%M') if when else '到時候'}我會來叫你。"

    def _judge(text):
        c.judge_calls.append(text)
        if judge_raises:
            raise gemini.GeminiError("網路炸了")
        return judge(text) if judge else None

    c.reply = lambda *a, **k: voice
    c.ask = lambda *a, **k: ("chat", None, voice)
    c.voice_schedule_ack = _sched_ack
    c.voice_promise_ack = lambda q, h: "好。"
    c.judge_timed_request = lambda t: None
    c.judge_self_promise = _judge
    return c


def _cfg(**over):
    base = dict(dry_run=False, telegram_chat_id="", scheduled_promise_enabled=True,
                promise_emit_enabled=True, promise_sched_ttl_sec=21600, timezone="Asia/Taipei",
                notify_cooldown_min=30, promise_reply_bridge_enabled=False, promise_ledger_enabled=True,
                sched_leave_autoarm_enabled=True, deferred_promise_enabled=True,
                promise_llm_rescue_enabled=True, bot_self_promise_enabled=True)
    base.update(over)
    return SimpleNamespace(**base)


def _msg(text, when):
    return {"message": {"chat": {"id": 1}, "text": text, "date": when.timestamp()}}


_TURN_KEYS = ("self_promise_ctx", "self_promise_skip", "promise_guard_llm_no",
              "keep_claim_ground", "ground_now", "last_reply")


class SelfPromiseE2ETest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        for k in _TURN_KEYS:
            monitor._TURN.pop(k, None)   # 隔離跨測試殘留

    def tearDown(self):
        for k in _TURN_KEYS:
            monitor._TURN.pop(k, None)

    def _state(self):
        s = State(os.path.join(self.tmp, "s.json"))
        s.owner_folder_id = "F"
        return s

    def _turn(self, st, voice, judge=None, judge_raises=False, cfg=None, text="隨便聊聊", when=NOW):
        cl = FakeClient()
        co = _coach(voice=voice, judge=judge, judge_raises=judge_raises)
        with patch("telegram_monitor.coach.build_memory_brief", return_value="brief"):
            monitor.handle_message(_msg(text, when), co, _Benign(), {"meta": {}}, None, st, cl,
                                   cfg or _cfg(), TZ)
        return cl, co

    def test_t1_bot_self_promise_booked(self):
        """T1（截圖重演）：21:50 bot 說出「明天早上 11 點，我會過來…」→ 入帳 7/11 11:00、origin='self'。"""
        st = self._state()
        cl, co = self._turn(st, BOT_PROMISE, judge=lambda t: (True, "跟你說讀誦經書的進展"))
        self.assertIn(BOT_PROMISE, "".join(cl.sent))                       # 那句真的送出去了
        proms = st.scheduled_promises or []
        self.assertEqual(len(proms), 1)
        self.assertEqual(round(proms[0]["target_ts"]), round(TARGET))     # 時刻＝temporal 解 bot 那句話
        self.assertEqual(proms[0].get("origin"), "self")
        self.assertEqual(proms[0].get("status"), "pending")
        self.assertFalse(proms[0].get("fulfilled"))
        self.assertIn("明天早上 11 點", proms[0].get("made_text") or "")   # made_text＝bot 原句
        self.assertEqual(proms[0].get("behavior"), "跟你說讀誦經書的進展")  # LLM 動作（已 sanitize）
        self.assertEqual(len(co.judge_calls), 1)

    def test_t2_same_sentence_next_turn_deduped(self):
        """T2：下一輪同句重送 → 仍 1 筆（(c) 時刻鄰近去重，且不再燒 LLM）。"""
        st = self._state()
        self._turn(st, BOT_PROMISE, judge=lambda t: (True, "跟你說讀誦經書的進展"))
        cl2, co2 = self._turn(st, BOT_PROMISE, judge=lambda t: (True, "不該再入帳"))
        self.assertEqual(len(st.scheduled_promises or []), 1)
        self.assertEqual(len(co2.judge_calls), 0)                          # 去重在 LLM 之前＝零呼叫

    def test_t3_user_request_ack_not_double_booked(self):
        """T3（實測④雙帳雷）：「12:18叫我」正常入帳＋ack（含 12:18＋我會）→ 帳上恰 1 筆（skip 擋 ack 重入）。"""
        st = self._state()
        cl, co = self._turn(st, "嗯。", judge=lambda t: (True, "不該被呼叫"),
                            text="12:18叫我", when=NOW_MORNING)
        joined = "".join(cl.sent)
        self.assertIn("12:18", joined)                                     # ack 帶程式時鐘算的鐘點
        self.assertIn("我會", joined)                                      # ack 本身就長著結構網會中的樣子
        proms = st.scheduled_promises or []
        self.assertEqual(len(proms), 1)                                    # 只有使用者那筆、ack 沒被重入帳
        self.assertNotEqual(proms[0].get("origin"), "self")
        self.assertEqual(len(co.judge_calls), 0)

    def test_t4_ledger_recital_context_excluded(self):
        """T4（實測②③複誦陷阱）：帳上已有 7/11 11:00、11:06 問「我們之間還有什麼承諾」、帳本回覆違規吐
        「我會記得在明天早上十一點…」（temporal 會滾成 7/12 11:00 未來錨、時刻去重救不了）→
        route.kind='promise_ledger' 語境排除＝筆數不變、judge 不得被呼叫。"""
        st = self._state()
        st.scheduled_promises = [{"target_ts": TARGET, "action": BOT_PROMISE[:300], "made_ts": NOW.timestamp(),
                                  "made_text": BOT_PROMISE[:300], "fulfilled": False,
                                  "behavior": "跟你說讀誦經書的進展", "status": "pending", "origin": "self"}]
        cl, co = self._turn(st, BOT_RECITAL, judge=lambda t: (True, "違規複誦不該被呼叫"),
                            text="我們之間還有什麼承諾", when=NOW_ASK)
        self.assertEqual(len(st.scheduled_promises or []), 1)              # 沒把複誦誤入帳成 7/12 11:00 新承諾
        self.assertEqual(round(st.scheduled_promises[0]["target_ts"]), round(TARGET))
        self.assertEqual(len(co.judge_calls), 0)

    def test_t5_no_time_anchor_no_booking(self):
        """T5：「我會努力變得更好。」→ 不入帳、不燒 LLM（temporal 空＝(b) 層濾掉）。"""
        st = self._state()
        cl, co = self._turn(st, BOT_EFFORT, judge=lambda t: (True, "不該被呼叫"))
        self.assertEqual(len(st.scheduled_promises or []), 0)
        self.assertEqual(len(co.judge_calls), 0)

    def test_t6_judge_no_or_error_fail_safe(self):
        """T6：judge 回 (False,'') 與 raise GeminiError 兩例 → 皆不入帳（失敗安全）。"""
        for kw in (dict(judge=lambda t: (False, "")), dict(judge_raises=True)):
            st = self._state()
            for k in _TURN_KEYS:
                monitor._TURN.pop(k, None)
            self._turn(st, BOT_PROMISE, **kw)
            self.assertEqual(len(st.scheduled_promises or []), 0, kw)

    def test_t7_proposal_question_gated_by_judge(self):
        """T7：「明天早上 11 點我過來，這樣可以嗎？」judge (False,'') → 不入帳、judge 恰被呼叫一次
        （結構網＋temporal 有開火、由語意閘擋下＝提案徵詢不是承諾）。"""
        st = self._state()
        cl, co = self._turn(st, BOT_PROPOSAL, judge=lambda t: (False, ""))
        self.assertEqual(len(st.scheduled_promises or []), 0)
        self.assertEqual(len(co.judge_calls), 1)

    def test_t8_flag_off_bitwise_head_behavior(self):
        """T8（旗標消融）：BOT_SELF_PROMISE=0 → T1 同輸入零入帳、_TURN 無 self_promise_ctx 鍵、judge 零呼叫。"""
        st = self._state()
        cl, co = self._turn(st, BOT_PROMISE, judge=lambda t: (True, "不該被呼叫"),
                            cfg=_cfg(bot_self_promise_enabled=False))
        self.assertEqual(len(st.scheduled_promises or []), 0)
        self.assertNotIn("self_promise_ctx", monitor._TURN)
        self.assertEqual(len(co.judge_calls), 0)

    def test_llm_clock_in_action_discarded(self):
        """【§1.12 鐵律】judge 動作字串夾時刻 → target 仍是 temporal 解的 7/11 11:00、behavior 不含 11:00。"""
        st = self._state()
        self._turn(st, BOT_PROMISE, judge=lambda t: (True, "在11:00跟你說讀誦經書的進展"))
        proms = st.scheduled_promises or []
        self.assertEqual(len(proms), 1)
        self.assertEqual(round(proms[0]["target_ts"]), round(TARGET))
        self.assertNotIn("11:00", proms[0].get("behavior") or "")


class JudgeProtocolTest(unittest.TestCase):
    """persona 協定：沒有時間欄位＝LLM 無從回傳時刻；且明講「複誦既有/對帳/認錯/提案徵詢」都是『否』。"""

    def test_system_prompt_forbids_time(self):
        g = persona.SELF_PROMISE_JUDGE_SYSTEM
        self.assertIn("嚴禁", g)
        self.assertIn("時間由系統時鐘管理", g)
        self.assertIn("是", g)
        self.assertIn("否", g)
        self.assertIn("複誦", g)                                # 複誦既有約定＝『否』寫進協定（實測②③陷阱）

    def test_user_prompt_contains_text(self):
        self.assertIn("明天早上 11 點", persona.self_promise_judge_user(BOT_PROMISE))


class ConfigSyncTest(unittest.TestCase):
    def test_flag_exists_and_is_bool(self):
        os.environ.pop("BOT_SELF_PROMISE", None)
        cfg = config.Config.load()
        self.assertIsInstance(cfg.bot_self_promise_enabled, bool)

    def test_flag_off_env(self):
        with patch.dict(os.environ, {"BOT_SELF_PROMISE": "0"}):
            self.assertFalse(config.Config.load().bot_self_promise_enabled)


if __name__ == "__main__":
    unittest.main()
