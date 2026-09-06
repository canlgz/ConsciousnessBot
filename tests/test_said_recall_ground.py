"""🤝 §1.20 SAID_RECALL_GROUND：「說過/約過」質問接地＋帳本日期詞程式產出＋否認句誠實閘。

截圖根因鏈：
- 7/11 11:06 問「我們之間還有什麼承諾」——路由本就進 promise_ledger，但回覆時態還在「明天早上十一點」、
  接著把「明天」自算成「7 月 12 日（週日）」＝LLM 自算日期的幻覺。日期詞必須由程式依 target_ts 算出（今天/明天…）。
- 7/12 07:01 問「我不是跟你說過了？」——帳上明明有 07:00 叫醒、一分鐘前才 🤝 兌現完，聊天回覆卻說
  「我沒有真的聽到你說『明天早上七點要叫我起床』這句話」＝與自己的兌現訊息直接自相矛盾（最嚴重誠實違規）。
硬規則：帳本或 72h 內剛兌現有相符項時，任何路徑都不得吐「沒聽到你說」。旗標 SAID_RECALL_GROUND=0＝逐位元同現狀。
"""

import os
import tempfile
import unittest
from datetime import datetime, timezone, timedelta
from types import SimpleNamespace
from zoneinfo import ZoneInfo

from telegram_monitor import monitor, selfstate
from telegram_monitor.config import Config
from telegram_monitor.state import State

TZ = ZoneInfo("Asia/Taipei")
N1106 = datetime(2026, 7, 11, 3, 6, 0, tzinfo=timezone.utc)    # 台北 7/11 11:06（截圖時刻）
N0701 = datetime(2026, 7, 11, 23, 1, 0, tzinfo=timezone.utc)   # 台北 7/12 07:01


class FakeClient:
    def __init__(self):
        self.sent, self.stickers, self.dry_run = [], [], False

    def send(self, text):
        self.sent.append(text)
        return True

    def send_sticker(self, fid):
        self.stickers.append(fid)
        return True


def _cfg(**over):
    base = dict(dry_run=False, telegram_chat_id="", scheduled_promise_enabled=True,
                promise_emit_enabled=True, promise_sched_ttl_sec=21600, timezone="Asia/Taipei",
                notify_cooldown_min=30, promise_reply_bridge_enabled=False, promise_ledger_enabled=True,
                sched_leave_autoarm_enabled=True, deferred_promise_enabled=True,
                promise_llm_rescue_enabled=False, sticker_llm_rescue_enabled=False,
                promise_keep_claim_guard_enabled=True, promise_outcome_ground_enabled=True,
                bot_self_promise_enabled=False, promise_preempt_enabled=False,
                promise_said_ground_enabled=True)
    base.update(over)
    return SimpleNamespace(**base)


def _coach_chat(reply_text):
    """enabled 的假教練：fact_or_chat 走 ask、帳本走 reply——兩者都回固定字串（測否認閘的攔截面）。"""
    c = SimpleNamespace(enabled=True, api_key="k", model="m",
                        meter=SimpleNamespace(record=lambda *a, **k: None), asked=[])
    c.ask = lambda *a, **k: (c.asked.append(1) or ("chat", None, reply_text))
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


def _coach_ledger_fallback():
    """enabled 但 reply 拋 GeminiError → 帳本路由落到確定性 promise_ledger_text（測 dayword 退路字串）。"""
    from telegram_monitor import gemini
    def _boom(*a, **k):
        raise gemini.GeminiError("stub")
    c = _coach_chat("")
    c.reply = _boom
    return c


def _msg(text, when):
    return {"message": {"chat": {"id": 1}, "text": text, "date": when.timestamp()}}


def _reading_promise():
    """§1.18 那筆：7/10 21:50 立、target 7/11 11:00、origin=self。"""
    target = datetime(2026, 7, 11, 3, 0, 0, tzinfo=timezone.utc).timestamp()
    return {"target_ts": target, "behavior": "跟他說說讀誦經書的進展", "origin": "self",
            "made_ts": target - 47400, "made_text": "明天早上 11 點，我會過來這裡…", "fulfilled": False}


def _wake_fulfilled():
    """07:00 叫醒、07:00:05 剛兌現。"""
    target = datetime(2026, 7, 11, 23, 0, 0, tzinfo=timezone.utc).timestamp()
    return {"target_ts": target, "behavior": "叫他起床", "made_ts": target - 28740,
            "made_text": "明天早上七點叫我起床喔", "fulfilled": True, "status": "fulfilled",
            "fulfilled_ts": target + 5}


class SaidKindTest(unittest.TestCase):
    def test_said_forms_hit(self):
        for t in ("我不是跟你說過了？", "我不是說過了嗎", "昨天說明天11點", "你忘了我說過",
                  "我有告訴過你"):
            self.assertEqual(selfstate.promise_status_kind(t), "said", t)

    def test_daily_confront_not_hit(self):
        for t in ("我說過不要遲到", "昨天說的電影很好看", "我們昨天說到哪了"):
            self.assertNotEqual(selfstate.promise_status_kind(t), "said", t)

    def test_baseline_five_kinds_unchanged(self):
        # 期望值＝主管錄的行為指紋基線（base4026_fn.txt）逐格照抄——'said' 排最末不得影響任何一格
        for t, k in (("結果呢", "outcome"), ("做到了嗎", "outcome"), ("你來了？", "outcome"),
                     ("時間到了沒", "timeup"), ("你有記進帳本？", ""),
                     ("比賽結果呢", ""), ("說好的呢", "outcome")):
            self.assertEqual(selfstate.promise_status_kind(t), k, t)


class DaywordTest(unittest.TestCase):
    def test_dayword_math(self):
        now = N1106.timestamp()
        target_today = datetime(2026, 7, 11, 3, 0, 0, tzinfo=timezone.utc).timestamp()
        self.assertEqual(selfstate._dayword_local(target_today, now, TZ), "今天")
        self.assertEqual(selfstate._dayword_local(target_today + 86400, now, TZ), "明天")
        self.assertEqual(selfstate._dayword_local(target_today - 86400, now, TZ), "昨天")

    def test_ledger_text_dayword_on(self):
        st = State(os.path.join(tempfile.mkdtemp(), "s.json"))
        st.scheduled_promises = [_reading_promise()]
        out = selfstate.promise_ledger_text(st, N1106, TZ, ttl=21600, dayword=True)
        self.assertIn("今天", out)
        self.assertIn("11:00", out)
        self.assertNotIn("明天", out)

    def test_ledger_default_bit_identical(self):
        st = State(os.path.join(tempfile.mkdtemp(), "s.json"))
        st.scheduled_promises = [_reading_promise()]
        base = selfstate.promise_ledger_text(st, N1106, TZ, ttl=21600)
        self.assertNotIn("今天", base)              # 預設 dayword=False＝逐位元同現狀（無日期詞）
        facts = selfstate.promise_ledger_facts(st, N1106, TZ, ttl=21600)
        self.assertNotIn("今天 11:00", facts)


class RouteReplayTest(unittest.TestCase):
    """截圖 11:06 重演：盤點與 said 質問都要接到帳本、時刻正確。"""

    def _run(self, text, proms, cfg=None, coach=None, when=N1106):
        tmp = tempfile.mkdtemp()
        st = State(os.path.join(tmp, "s.json")); st.owner_folder_id = "F"
        st.scheduled_promises = [dict(p) for p in proms]
        cl = FakeClient()
        co = coach if coach is not None else _coach_ledger_fallback()
        monitor.handle_message(_msg(text, when), co, BOOM, {"meta": {}}, SNAP, st, cl, cfg or _cfg(), TZ)
        return st, cl

    def test_inventory_grounded_today_not_tomorrow(self):
        _, cl = self._run("我們之間還有什麼承諾", [_reading_promise()])
        reply = "\n".join(cl.sent)
        self.assertTrue(reply)
        self.assertIn("11:00", reply)
        self.assertIn("今天", reply)
        self.assertNotIn("明天", reply)                    # 11:06 截圖：時態還在「明天」＝must not

    def test_said_time_question_routes_to_ledger(self):
        _, cl = self._run("昨天說明天11點", [_reading_promise()])
        reply = "\n".join(cl.sent)
        self.assertIn("11:00", reply)
        self.assertIn("今天", reply)

    def test_empty_ledger_no_hijack(self):
        co = _coach_chat("嗯，你說過什麼呢？")
        _, cl = self._run("我不是跟你說過了？", [], coach=co)
        self.assertTrue(co.asked)                          # 空帳＝日常對質照落聊天（不劫持）

    def test_flag_off_bit_identical_route(self):
        co = _coach_chat("嗯。")
        _, cl = self._run("昨天說明天11點", [_reading_promise()],
                          cfg=_cfg(promise_said_ground_enabled=False), coach=co)
        self.assertTrue(co.asked)                          # 旗標關＝said 不搶＝照落聊天


class DenialGuardTest(unittest.TestCase):
    """截圖 07:01 重演：帳上有剛兌現的叫醒，聊天回覆卻否認「沒聽到你說」→ 整則替換誠實句。"""

    DENIAL = "我那時候看你說睡不著，心裡有點掛念，但我沒有真的聽到你說「明天早上七點要叫我起床」這句話。"

    def _run(self, reply_text, proms, cfg=None):
        tmp = tempfile.mkdtemp()
        st = State(os.path.join(tmp, "s.json")); st.owner_folder_id = "F"
        st.scheduled_promises = [dict(p) for p in proms]
        cl = FakeClient()
        co = _coach_ledger_fallback()          # said 路由走帳本 → 確定性退路
        monitor.handle_message(_msg("我不是跟你說過了？", N0701), co,
                               BOOM, {"meta": {}}, SNAP, st, cl, cfg or _cfg(), TZ)
        return "\n".join(cl.sent)

    def test_denial_replaced_with_honest(self):
        # said 路由的狀態閘會先搶走這句 → 用不含 said 形的問法逼進聊天路徑再測否認閘
        tmp = tempfile.mkdtemp()
        st = State(os.path.join(tmp, "s.json")); st.owner_folder_id = "F"
        st.scheduled_promises = [_wake_fulfilled()]
        cl = FakeClient()
        monitor.handle_message(_msg("蛤？", N0701), _coach_chat(self.DENIAL),
                               BOOM, {"meta": {}}, SNAP, st, cl, _cfg(), TZ)
        reply = "\n".join(cl.sent)
        self.assertNotIn("沒有真的聽到", reply)     # 否認句整則被替換
        self.assertNotIn("沒聽到你說", reply)
        self.assertIn("07:00", reply)
        self.assertIn("叫他起床", reply)
        self.assertIn("你說過", reply)              # 替換成的是「有，你說過」誠實句

    def test_said_route_grounds_on_fulfilled(self):
        reply = self._run(self.DENIAL, [_wake_fulfilled()])
        self.assertNotIn("沒聽到", reply)                   # said 路由走帳本＝根本輪不到否認句
        self.assertIn("07:00", reply)

    def test_quote_attribution_not_intercepted(self):
        quoted = "你剛剛說我沒聽到你說，其實我有記著喔。"
        reply = self._run(quoted, [_wake_fulfilled()])
        # said 路由搶走 → 接地回覆；改走純聊天驗證引用不攔：
        tmp = tempfile.mkdtemp()
        st = State(os.path.join(tmp, "s.json")); st.owner_folder_id = "F"
        st.scheduled_promises = [_wake_fulfilled()]
        cl = FakeClient()
        monitor.handle_message(_msg("蛤？", N0701), _coach_chat(quoted),
                               BOOM, {"meta": {}}, SNAP, st, cl, _cfg(), TZ)
        self.assertIn("你剛剛說", "\n".join(cl.sent))       # 引用歸屬＝不是 bot 自己的否認＝不攔

    def test_flag_off_denial_passes_through(self):
        tmp = tempfile.mkdtemp()
        st = State(os.path.join(tmp, "s.json")); st.owner_folder_id = "F"
        st.scheduled_promises = [_wake_fulfilled()]
        cl = FakeClient()
        monitor.handle_message(_msg("蛤？", N0701), _coach_chat(self.DENIAL),
                               BOOM, {"meta": {}}, SNAP, st, cl,
                               _cfg(promise_said_ground_enabled=False), TZ)
        self.assertIn("沒有真的聽到", "\n".join(cl.sent))   # 旗標關＝逐位元同現狀（否認照過）


class ConfigSyncTest(unittest.TestCase):
    def test_flag_declared_and_env_synced(self):
        import re
        src = open("telegram_monitor/config.py", encoding="utf-8").read()
        self.assertIn("SAID_RECALL_GROUND", src)
        self.assertIn("promise_said_ground_enabled", src)
        env = open(".env.example", encoding="utf-8").read()
        self.assertRegex(env, re.compile(r"^SAID_RECALL_GROUND=1", re.M))


if __name__ == "__main__":
    unittest.main()
